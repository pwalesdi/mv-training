'use strict';
/* Plain-SVG charts for the athlete page - no library, works offline.
   Each chart is drawn at its container's pixel width (so axis text stays 11px), shows its values on
   hover and on keyboard focus (arrow keys) through the shared #tip element, and is always paired
   with a table that has the same numbers. Colors come from the CSS tokens in app.css. */

const Charts = (() => {
  const DAY = 864e5;
  const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const WD = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  const ms = iso => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10));
  const md = t => { const d = new Date(t); return `${MON[d.getUTCMonth()]} ${d.getUTCDate()}`; };
  const wmd = t => `${WD[new Date(t).getUTCDay()]} ${md(t)}`;
  const year = t => new Date(t).getUTCFullYear();
  const x1 = v => Math.round(v * 10) / 10;  // svg coordinates, one decimal
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const registry = new Map();
  let nextId = 1;

  function niceStep(span, n) {
    const raw = span / Math.max(1, n), p = 10 ** Math.floor(Math.log10(raw)), f = raw / p;
    return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10) * p;
  }
  const seasonStart = t => { const d = new Date(t); return Date.UTC(d.getUTCMonth() >= 6 ? d.getUTCFullYear() : d.getUTCFullYear() - 1, 6, 1); };
  const seasonName = t => { const y = year(seasonStart(t)); return `${y}-${String((y + 1) % 100).padStart(2, '0')}`; };

  // month starts in (t0, t1], every k months so labels sit at least minPx apart
  function monthTicks(t0, t1, pxPerDay, minPx = 56) {
    const k = [1, 2, 3, 6, 12].find(s => s * 30.4 * pxPerDay >= minPx) || 12;
    const out = [];
    let y = year(t0), m = new Date(t0).getUTCMonth();
    for (;;) {
      const t = Date.UTC(y, m, 1);
      if (t > t1) break;
      if (t >= t0 && m % k === 0) out.push(t);
      if (++m > 11) { m = 0; y++; }
    }
    return { ticks: out, k };
  }
  function tickLabel(t, i, k) {
    const d = new Date(t), m = d.getUTCMonth();
    return k >= 3 || m === 0 || i === 0 ? `${MON[m]} ’${String(d.getUTCFullYear()).slice(2)}` : MON[m];
  }

  /* ---- tooltip (shared #tip element; text via textContent only) ---- */
  const tipEl = () => document.getElementById('tip');
  function tipShow(lines, cx, cy) {
    const tip = tipEl();
    tip.replaceChildren(...lines.map(([text, cls]) => {
      const div = document.createElement('div');
      if (cls) div.className = cls;
      div.textContent = text;
      return div;
    }));
    tip.hidden = false;
    const w = tip.offsetWidth || 220;
    tip.style.left = `${Math.max(4, Math.min(cx + 14, window.innerWidth - w - 8))}px`;
    tip.style.top = `${cy + 16}px`;
  }
  const tipHide = () => { tipEl().hidden = true; };

  /* ---- period bars: one bar per Monday-Sunday week (or calendar month) on a time axis ---- */
  // rows: [{week_start, week_end, partial, ...}] sorted by date. opts:
  //   value(w)   the bar's number; null / undefined = no data that period (shaded, never drawn as 0)
  //   cls        bar class ('mbar' run miles, 'cbar' cross-training); height; label (screen readers)
  //   t0, t1     ms - share another chart's time axis so the two line up
  //   marker(w)  true -> a small dot above the bar (runs with no distance)
  //   note(w)    short text drawn above the bar (e.g. the month in progress)
  //   readout(w) tooltip lines [[text, class]] for a period with data
  //   span(w)    [start, end) ms of the row's period - default the week from week_start; unit 'week' | 'month'
  const weekSpan = w => { const a = ms(w.week_start); return [a, a + 7 * DAY]; };
  function weekBars(weeks, width, opts = {}) {
    const val = opts.value || (w => w.miles), span = opts.span || weekSpan, unit = opts.unit || 'week';
    const rows = weeks.filter(w => val(w) !== null && val(w) !== undefined);
    const spans = rows.map(span);
    const id = `ch${nextId++}`;
    const W = Math.max(360, width), H = opts.height || 230, L = 40, R = 14, T = 26, B = 28;
    const t0 = opts.t0 ?? spans[0][0], t1 = opts.t1 ?? spans[spans.length - 1][1];
    const ppd = (W - L - R) / ((t1 - t0) / DAY);
    const x = t => L + (t - t0) / DAY * ppd;
    const max = Math.max(1, ...rows.map(val));
    const step = niceStep(max, H < 200 ? 3 : 4), top = Math.ceil(max / step) * step;
    const y = v => T + (H - T - B) * (1 - v / top);
    const maxBar = opts.maxBar || 24;
    const gaps = [];
    let seen = t0;  // shade every stretch of a week or more without a row, edges included
    for (const [a, b] of spans) {
      if (a - seen >= 6.5 * DAY) gaps.push([seen, a]);
      seen = Math.max(seen, b);
    }
    if (t1 - seen >= 6.5 * DAY) gaps.push([seen, t1]);
    let s = '';
    for (let v = 0; v <= top + 1e-9; v += step) {
      s += `<line class="gl" x1="${L}" x2="${W - R}" y1="${x1(y(v))}" y2="${x1(y(v))}"/>`
        + `<text class="ax" x="${L - 6}" y="${x1(y(v) + 4)}" text-anchor="end">${+v.toFixed(2)}</text>`;
    }
    for (const [a, b] of gaps) {
      const gx = x(a), gw = x(b) - x(a);
      s += `<rect class="gap" x="${x1(gx)}" y="${T}" width="${x1(gw)}" height="${H - T - B}"/>`;
      if (gw > 50) s += `<text class="gaplbl" x="${x1(gx + gw / 2)}" y="${T + 14}" text-anchor="middle">no data</text>`;
    }
    // season boundaries (1 July); the first season is named at the left edge
    const bounds = [];
    for (let b = seasonStart(t0) + 0; b <= t1; b = Date.UTC(year(b) + 1, 6, 1)) if (b > t0) bounds.push(b);
    if (!bounds.length || x(bounds[0]) - L > 70) s += `<text class="seasonlbl" x="${L + 2}" y="${T - 10}">${seasonName(t0)}</text>`;
    for (const b of bounds) {
      s += `<line class="season" x1="${x1(x(b))}" x2="${x1(x(b))}" y1="${T - 20}" y2="${H - B}"/>`
        + `<text class="seasonlbl" x="${x1(x(b) + 5)}" y="${T - 10}">${seasonName(b)}</text>`;
    }
    const { ticks, k } = monthTicks(t0, t1, ppd);
    ticks.forEach((t, i) => {
      s += `<line class="tick" x1="${x1(x(t))}" x2="${x1(x(t))}" y1="${H - B}" y2="${H - B + 4}"/>`
        + `<text class="ax" x="${x1(x(t))}" y="${H - 8}" text-anchor="middle">${tickLabel(t, i, k)}</text>`;
    });
    s += `<line class="base" x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}"/>`;
    rows.forEach((w, i) => {
      const [a, b] = spans[i], slot = (b - a) / DAY * ppd, bw = slot >= 6 ? Math.min(maxBar, slot - 2) : Math.max(1, slot * 0.7);
      const v = val(w), cx = x(a) + slot / 2, xs = cx - bw / 2, yy = y(v), base = y(0);
      const h = base - yy, r = Math.min(4, bw / 2, h);
      const d = h <= 0 ? `M${x1(xs)},${x1(base)}h${x1(bw)}` // a zero week: nothing to draw but keep the node
        : `M${x1(xs)},${x1(base)}V${x1(yy + r)}Q${x1(xs)},${x1(yy)} ${x1(xs + r)},${x1(yy)}H${x1(xs + bw - r)}Q${x1(xs + bw)},${x1(yy)} ${x1(xs + bw)},${x1(yy + r)}V${x1(base)}Z`;
      s += `<path class="wbar ${opts.cls || 'mbar'}${w.partial ? ' partial' : ''}" data-i="${i}" data-v="${v}" d="${d}"/>`;
      if (opts.marker && opts.marker(w)) s += `<circle class="nd" cx="${x1(cx)}" cy="${x1(Math.min(yy, base) - 7)}" r="3.5"/>`;
      const note = opts.note && opts.note(w);
      if (note) {
        const right = cx > W - R - 40;
        s += `<text class="barnote" x="${x1(right ? xs + bw : cx)}" y="${x1(Math.min(yy, base) - 6)}" text-anchor="${right ? 'end' : 'middle'}">${esc(note)}</text>`;
      }
    });
    s += `<line class="xhair" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>`
      + `<rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}"/>`;
    registry.set(id, { kind: 'weeks', weeks: rows, spans, unit, t0, x, ppd, L, readout: opts.readout || (w => [[String(val(w)), 'tv']]) });
    const label = `${opts.label || 'Weekly values'}, ${rows.length} ${unit}s from ${md(t0)}, ${year(t0)} to ${md(t1 - DAY)}, ${year(t1 - DAY)}. Use the arrow keys to read each ${unit}; the table below has every value.`;
    return `<svg data-chart="${id}" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" tabindex="0" role="img" aria-label="${esc(label)}">${s}</svg>`;
  }
  // calendar months: rows with month_start / month_end (YYYY-MM-DD)
  function monthBars(months, width, opts = {}) {
    return weekBars(months, width, { maxBar: 44, ...opts, unit: 'month', span: m => [ms(m.month_start), ms(m.month_end) + DAY] });
  }

  // the period (week or calendar month) that time t falls in, for a stretch with no data
  function emptySlot(c, t) {
    if (c.unit === 'month') {
      const d = new Date(t), a = Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1);
      return [a, Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1)];
    }
    const a = c.t0 + Math.floor((t - c.t0) / (7 * DAY)) * 7 * DAY;
    return [a, a + 7 * DAY];
  }
  function weekReadout(c, i, t) {
    if (i >= 0) return c.readout(c.weeks[i]);
    const [a, b] = emptySlot(c, t);
    if (c.unit === 'month') return [['no data', 'tv'], [`${MON[new Date(a).getUTCMonth()]} ${year(a)}`]];
    return [['no data', 'tv'], [`Week ${wmd(a)} – ${wmd(b - DAY)}, ${year(b - DAY)}`]];
  }

  function wireWeeks(svg, c) {
    const bars = [...svg.querySelectorAll('.wbar')], hair = svg.querySelector('.xhair');
    let cur = -1;
    const mark = (i, t) => {
      bars.forEach(b => b.classList.toggle('on', +b.dataset.i === i));
      const [a, b] = i >= 0 ? c.spans[i] : emptySlot(c, t), cx = (c.x(a) + c.x(b)) / 2;
      hair.setAttribute('x1', x1(cx)); hair.setAttribute('x2', x1(cx)); hair.setAttribute('visibility', 'visible');
      return cx;
    };
    const clear = () => { bars.forEach(b => b.classList.remove('on')); hair.setAttribute('visibility', 'hidden'); tipHide(); };
    svg.addEventListener('pointermove', e => {
      const box = svg.getBoundingClientRect(), px = (e.clientX - box.left) * (svg.viewBox.baseVal.width / box.width);
      if (px < c.L) { clear(); return; }
      const t = c.t0 + (px - c.L) / c.ppd * DAY;
      const i = c.spans.findIndex(([a, b]) => t >= a && t < b);
      mark(i, t);
      tipShow(weekReadout(c, i, t), e.clientX, e.clientY);
    });
    svg.addEventListener('pointerleave', clear);
    const showKey = () => {
      const cx = mark(cur), box = svg.getBoundingClientRect(), sc = box.width / svg.viewBox.baseVal.width;
      tipShow(weekReadout(c, cur, 0), box.left + cx * sc, box.top + 30);
    };
    svg.addEventListener('focus', () => { cur = c.weeks.length - 1; showKey(); });
    svg.addEventListener('blur', clear);
    svg.addEventListener('keydown', e => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      cur = Math.max(0, Math.min(c.weeks.length - 1, cur + (e.key === 'ArrowRight' ? 1 : -1)));
      showKey();
    });
  }

  /* ---- race times: faster is higher on the axis ---- */
  const clock = v => {  // seconds -> m:ss(.s) for axis ticks
    const m = Math.floor(v / 60), sec = v - m * 60, r = Math.round(sec * 10) / 10;
    const ss = (r % 1 ? r.toFixed(1) : String(r)).padStart(r % 1 ? 4 : 2, '0');
    return m ? `${m}:${ss}` : `${r}`;
  };
  function timeStep(span, n) {
    return [0.5, 1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600].find(s => span / s <= n) || 600;
  }
  function yTime(secs, T, plotH, n = 4) {
    let lo = Math.min(...secs), hi = Math.max(...secs);
    const pad = Math.max(0.5, (hi - lo) * 0.12);
    lo -= pad; hi += pad;
    const step = timeStep(hi - lo, n), a = Math.floor(lo / step) * step, b = Math.ceil(hi / step) * step;
    const y = v => T + plotH * (v - a) / (b - a);  // smaller time (faster) nearer the top
    const ticks = [];
    for (let v = a; v <= b + 1e-9; v += step) ticks.push(v);
    return { y, ticks };
  }
  // faded weekly run miles behind race marks, on the chart's own time axis (xt: ms -> px). o: {bars: weeks
  // [{week_start, miles, partial}], barMax: miles drawn at 90% of the plot height, t0, t1}. No second axis: the
  // heights are relative to the athlete's biggest week (named in the chart key) and the hover gives the miles.
  function backBars(o, xt, T, H, B) {
    if (!o.bars || !o.bars.length || !(o.barMax > 0)) return '';
    const plotH = H - T - B;
    let s = '';
    for (const w of o.bars) {
      const a = ms(w.week_start), b = a + 7 * DAY, v = Number(w.miles);
      if (b <= o.t0 || a >= o.t1 || !(v > 0)) continue;
      const xa = Math.max(xt(a), xt(o.t0)), xb = Math.min(xt(b), xt(o.t1)), bw = Math.max(1, (xb - xa) * 0.8);
      const h = plotH * 0.9 * Math.min(1, v / o.barMax);
      s += `<rect class="obar${w.partial ? ' partial' : ''}" x="${x1(xa + (xb - xa - bw) / 2)}" y="${x1(H - B - h)}" width="${x1(bw)}" height="${x1(h)}"/>`;
    }
    return s;
  }
  // pts: [{t (ms), seconds, lines (tooltip rows), cls, label?}] - sorted by time; breaks: start a new line segment
  function pointsChart(kind, pts, W, H, L, R, T, B, x, yt, xAxis, aria, breakAt, back = '') {
    const id = `ch${nextId++}`;
    let s = back;
    for (const v of yt.ticks) {
      s += `<line class="gl" x1="${L}" x2="${W - R}" y1="${x1(yt.y(v))}" y2="${x1(yt.y(v))}"/>`
        + `<text class="ax" x="${L - 6}" y="${x1(yt.y(v) + 4)}" text-anchor="end">${clock(v)}</text>`;
    }
    s += xAxis;
    const xy = pts.map(p => [x(p), yt.y(p.seconds)]);
    let d = '';
    pts.forEach((p, i) => { d += `${i === 0 || breakAt(pts[i - 1], p) ? 'M' : 'L'}${x1(xy[i][0])},${x1(xy[i][1])}`; });
    s += `<path class="pline" d="${d}"/>`;
    pts.forEach((p, i) => {
      s += `<circle class="pt ${p.cls || ''}" data-i="${i}" data-secs="${p.seconds}" cx="${x1(xy[i][0])}" cy="${x1(xy[i][1])}" r="4.5"/>`;
    });
    const best = pts.findIndex(p => p.label);
    if (best >= 0) {
      const [bx, by] = xy[best], right = bx > W - R - 90;
      s += `<text class="plbl" x="${x1(bx + (right ? -9 : 9))}" y="${x1(Math.max(T + 10, by - 8))}" text-anchor="${right ? 'end' : 'start'}">${esc(pts[best].label)}</text>`;
    }
    s += `<line class="xhair" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/><circle class="ring" r="8" visibility="hidden"/>`
      + `<rect class="hit" x="${L}" y="${T - 6}" width="${W - L - R}" height="${H - T - B + 12}"/>`;
    registry.set(id, { kind: 'points', pts, xy, L, R, W });
    return `<svg data-chart="${id}" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" tabindex="0" role="img" aria-label="${esc(aria)}">${s}</svg>`;
  }
  function yearTicks(t0, t1, x, H, B, ppd) {
    const span = (t1 - t0) / DAY;
    let s = '';
    if (span > 420) {
      const first = Date.UTC(year(t0) + 1, 0, 1);
      if (x(first) - x(t0) > 46) s += `<text class="ax" x="${x1(x(t0) + 2)}" y="${H - 8}" text-anchor="start">${year(t0 + 30 * DAY)}</text>`;
      for (let y = year(t0) + 1; Date.UTC(y, 0, 1) <= t1; y++) {
        const t = Date.UTC(y, 0, 1);
        s += `<line class="tick" x1="${x1(x(t))}" x2="${x1(x(t))}" y1="${H - B}" y2="${H - B + 4}"/><text class="ax" x="${x1(x(t))}" y="${H - 8}" text-anchor="middle">${y}</text>`;
      }
      return s;
    }
    const { ticks, k } = monthTicks(t0, t1, ppd);
    ticks.forEach((t, i) => { s += `<line class="tick" x1="${x1(x(t))}" x2="${x1(x(t))}" y1="${H - B}" y2="${H - B + 4}"/><text class="ax" x="${x1(x(t))}" y="${H - 8}" text-anchor="middle">${tickLabel(t, i, k)}</text>`; });
    return s;
  }
  // one track event over time; o: {t0, t1, width, event, bars?, barMax?}. The line breaks between seasons.
  function progression(pts, o) {
    const W = Math.max(360, o.width), H = 180, L = 50, R = 16, T = 18, B = 26;
    const ppd = (W - L - R) / ((o.t1 - o.t0) / DAY);
    const xt = t => L + (t - o.t0) / DAY * ppd, x = p => xt(p.t);
    const yt = yTime(pts.map(p => p.seconds), T, H - T - B);
    const axis = yearTicks(o.t0, o.t1, xt, H, B, ppd) + `<line class="base" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>`;
    return pointsChart('progression', pts, W, H, L, R, T, B, x, yt, axis,
      `${o.event}: ${pts.length} timed races${o.bars ? ', with weekly run miles behind them' : ''}; the table below lists every one`,
      (a, b) => a.season !== b.season || b.t - a.t > 90 * DAY, backBars(o, xt, T, H, B));
  }
  // one XC course, its races in date order on a time axis (usually one a year), each labeled with its year;
  // o: {bars?, barMax?} for the weekly run miles behind them
  function course(pts, width, o = {}) {
    const W = Math.max(260, width), H = 140, L = 50, R = 24, T = 16, B = 24;
    const first = pts[0].t, last = pts[pts.length - 1].t, pad = Math.max(45 * DAY, (last - first) * 0.08);
    const t0 = first - pad, t1 = last + pad, ppd = (W - L - R) / ((t1 - t0) / DAY);
    const xt = t => L + (t - t0) / DAY * ppd, x = p => xt(p.t);
    const yt = yTime(pts.map(p => p.seconds), T, H - T - B, 3);
    let axis = `<line class="base" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>`;
    pts.forEach(p => { axis += `<text class="ax" x="${x1(x(p))}" y="${H - 8}" text-anchor="middle">${esc(p.xlabel)}</text>`; });
    return pointsChart('course', pts, W, H, L, R, T, B, x, yt, axis,
      `${pts.length} races on this course${o.bars ? ', with weekly run miles behind them' : ''}; the table lists each`, () => false,
      backBars({ ...o, t0, t1 }, xt, T, H, B));
  }

  function wirePoints(svg, c) {
    const hair = svg.querySelector('.xhair'), ring = svg.querySelector('.ring');
    let cur = -1;
    const mark = i => {
      const [px, py] = c.xy[i];
      hair.setAttribute('x1', x1(px)); hair.setAttribute('x2', x1(px)); hair.setAttribute('visibility', 'visible');
      ring.setAttribute('cx', x1(px)); ring.setAttribute('cy', x1(py)); ring.setAttribute('visibility', 'visible');
      return [px, py];
    };
    const clear = () => { hair.setAttribute('visibility', 'hidden'); ring.setAttribute('visibility', 'hidden'); tipHide(); };
    const nearest = (px, py) => {  // nearest by x; ties (same day) by y
      let bi = -1, bd = Infinity;
      c.xy.forEach(([x, y], i) => { const d = Math.abs(x - px) * 4 + Math.abs(y - py); if (d < bd) { bd = d; bi = i; } });
      return bi;
    };
    svg.addEventListener('pointermove', e => {
      const box = svg.getBoundingClientRect(), sc = svg.viewBox.baseVal.width / box.width;
      const px = (e.clientX - box.left) * sc, py = (e.clientY - box.top) * sc;
      if (px < c.L - 4 || px > c.W - c.R + 4) { clear(); return; }
      cur = nearest(px, py);
      if (cur < 0) return;
      mark(cur);
      tipShow(c.pts[cur].lines, e.clientX, e.clientY);
    });
    svg.addEventListener('pointerleave', clear);
    const showKey = () => {
      const [px, py] = mark(cur), box = svg.getBoundingClientRect(), sc = box.width / svg.viewBox.baseVal.width;
      tipShow(c.pts[cur].lines, box.left + px * sc, box.top + py * sc);
    };
    svg.addEventListener('focus', () => { cur = c.pts.length - 1; showKey(); });
    svg.addEventListener('blur', clear);
    svg.addEventListener('keydown', e => {
      if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
      e.preventDefault();
      cur = Math.max(0, Math.min(c.pts.length - 1, cur + (e.key === 'ArrowRight' ? 1 : -1)));
      showKey();
    });
  }

  // attach hover / keyboard handlers to every chart rendered inside root
  function wire(root) {
    for (const svg of root.querySelectorAll('svg[data-chart]')) {
      const c = registry.get(svg.dataset.chart);
      if (!c || svg.dataset.wired) continue;
      svg.dataset.wired = '1';
      if (c.kind === 'weeks') wireWeeks(svg, c);
      else if (c.kind === 'points') wirePoints(svg, c);
    }
  }
  function reset() { registry.clear(); tipHide(); }

  return { weekBars, monthBars, progression, course, wire, reset, seasonName, ms, DAY, wmd, year };
})();
