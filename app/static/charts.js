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

  /* ---- weekly mileage: one bar per Monday-Sunday week on a time axis ---- */
  // weeks: [{week_start, week_end, miles, partial, note}] sorted by week_start
  function mileage(weeks, width) {
    const id = `ch${nextId++}`;
    const W = Math.max(360, width), H = 230, L = 40, R = 14, T = 26, B = 28;
    const t0 = ms(weeks[0].week_start), t1 = ms(weeks[weeks.length - 1].week_start) + 7 * DAY;
    const ppd = (W - L - R) / ((t1 - t0) / DAY);
    const x = t => L + (t - t0) / DAY * ppd;
    const max = Math.max(1, ...weeks.map(w => w.miles));
    const step = niceStep(max, 4), top = Math.ceil(max / step) * step;
    const y = v => T + (H - T - B) * (1 - v / top);
    const slot = 7 * ppd, bw = slot >= 6 ? Math.min(24, slot - 2) : Math.max(1, slot * 0.7);
    const gaps = [];
    for (let i = 1; i < weeks.length; i++) {
      const end = ms(weeks[i - 1].week_start) + 7 * DAY, next = ms(weeks[i].week_start);
      if (next - end >= 6.5 * DAY) gaps.push([end, next]);
    }
    let s = '';
    for (let v = 0; v <= top + 1e-9; v += step) {
      s += `<line class="gl" x1="${L}" x2="${W - R}" y1="${x1(y(v))}" y2="${x1(y(v))}"/>`
        + `<text class="ax" x="${L - 6}" y="${x1(y(v) + 4)}" text-anchor="end">${+v.toFixed(1)}</text>`;
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
    weeks.forEach((w, i) => {
      const cx = x(ms(w.week_start)) + slot / 2, xs = cx - bw / 2, yy = y(w.miles), base = y(0);
      const h = base - yy, r = Math.min(4, bw / 2, h);
      const d = h <= 0 ? `M${x1(xs)},${x1(base)}h${x1(bw)}` // a zero week: nothing to draw but keep the node
        : `M${x1(xs)},${x1(base)}V${x1(yy + r)}Q${x1(xs)},${x1(yy)} ${x1(xs + r)},${x1(yy)}H${x1(xs + bw - r)}Q${x1(xs + bw)},${x1(yy)} ${x1(xs + bw)},${x1(yy + r)}V${x1(base)}Z`;
      s += `<path class="mbar${w.partial ? ' partial' : ''}" data-i="${i}" data-miles="${w.miles}" d="${d}"/>`;
    });
    s += `<line class="xhair" x1="0" x2="0" y1="${T}" y2="${H - B}" visibility="hidden"/>`
      + `<rect class="hit" x="${L}" y="${T}" width="${W - L - R}" height="${H - T - B}"/>`;
    registry.set(id, { kind: 'mileage', weeks, t0, x, slot, ppd, L });
    const label = `Weekly mileage, ${weeks.length} weeks from ${md(t0)}, ${year(t0)} to ${md(t1 - DAY)}, ${year(t1 - DAY)}. Use the arrow keys to read each week; the table below has every value.`;
    return `<svg data-chart="${id}" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" tabindex="0" role="img" aria-label="${esc(label)}">${s}</svg>`;
  }

  function mileageReadout(c, svg, i, t) {
    const w = i >= 0 ? c.weeks[i] : null;
    if (w) {
      const a = ms(w.week_start);
      return [[`${w.miles} mi`, 'tv'], [`Week ${wmd(a)} – ${wmd(ms(w.week_end))}, ${year(ms(w.week_end))}`],
        ...(w.partial ? [[`Partial week${w.note ? `: ${w.note}` : ''}`, 'tn']] : w.note ? [[w.note, 'tn']] : [])];
    }
    const a = c.t0 + Math.floor((t - c.t0) / (7 * DAY)) * 7 * DAY;
    return [['no data', 'tv'], [`Week ${wmd(a)} – ${wmd(a + 6 * DAY)}, ${year(a + 6 * DAY)}`]];
  }

  function wireMileage(svg, c) {
    const bars = [...svg.querySelectorAll('.mbar')], hair = svg.querySelector('.xhair');
    let cur = -1;
    const mark = (i, slotT) => {
      bars.forEach(b => b.classList.toggle('on', +b.dataset.i === i));
      const cx = i >= 0 ? c.x(ms(c.weeks[i].week_start)) + c.slot / 2 : c.x(slotT) + c.slot / 2;
      hair.setAttribute('x1', x1(cx)); hair.setAttribute('x2', x1(cx)); hair.setAttribute('visibility', 'visible');
      return cx;
    };
    const clear = () => { bars.forEach(b => b.classList.remove('on')); hair.setAttribute('visibility', 'hidden'); tipHide(); };
    svg.addEventListener('pointermove', e => {
      const box = svg.getBoundingClientRect(), px = (e.clientX - box.left) * (svg.viewBox.baseVal.width / box.width);
      if (px < c.L) { clear(); return; }
      const t = c.t0 + (px - c.L) / c.ppd * DAY;
      const i = c.weeks.findIndex(w => t >= ms(w.week_start) && t < ms(w.week_start) + 7 * DAY);
      const slotT = c.t0 + Math.floor((t - c.t0) / (7 * DAY)) * 7 * DAY;
      mark(i, slotT);
      tipShow(mileageReadout(c, svg, i, t), e.clientX, e.clientY);
    });
    svg.addEventListener('pointerleave', clear);
    const showKey = () => {
      const cx = mark(cur), box = svg.getBoundingClientRect(), sc = box.width / svg.viewBox.baseVal.width;
      tipShow(mileageReadout(c, svg, cur, 0), box.left + cx * sc, box.top + 30);
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
  // pts: [{t (ms), seconds, lines (tooltip rows), cls, label?}] - sorted by time; breaks: start a new line segment
  function pointsChart(kind, pts, W, H, L, R, T, B, x, yt, xAxis, aria, breakAt) {
    const id = `ch${nextId++}`;
    let s = '';
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
  // one track event over time; o: {t0, t1, width, event}. The line breaks between seasons.
  function progression(pts, o) {
    const W = Math.max(360, o.width), H = 180, L = 50, R = 16, T = 18, B = 26;
    const ppd = (W - L - R) / ((o.t1 - o.t0) / DAY);
    const x = p => L + (p.t - o.t0) / DAY * ppd;
    const yt = yTime(pts.map(p => p.seconds), T, H - T - B);
    const axis = yearTicks(o.t0, o.t1, t => L + (t - o.t0) / DAY * ppd, H, B, ppd) + `<line class="base" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>`;
    return pointsChart('progression', pts, W, H, L, R, T, B, x, yt, axis,
      `${o.event}: ${pts.length} timed races; the table below lists every one`,
      (a, b) => a.season !== b.season || b.t - a.t > 90 * DAY);
  }
  // one XC course, its races side by side in date order (usually one a year)
  function course(pts, width) {
    const W = Math.max(260, width), H = 140, L = 50, R = 24, T = 16, B = 24;
    const n = pts.length, gap = n > 1 ? (W - L - R - 40) / (n - 1) : 0;
    const x = p => L + 20 + pts.indexOf(p) * gap;
    const yt = yTime(pts.map(p => p.seconds), T, H - T - B, 3);
    let axis = `<line class="base" x1="${L}" x2="${W - R}" y1="${H - B}" y2="${H - B}"/>`;
    pts.forEach(p => { axis += `<text class="ax" x="${x1(x(p))}" y="${H - 8}" text-anchor="middle">${esc(p.xlabel)}</text>`; });
    return pointsChart('course', pts, W, H, L, R, T, B, x, yt, axis, `${n} races on this course; the table lists each`, () => false);
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
      if (c.kind === 'mileage') wireMileage(svg, c);
      else if (c.kind === 'points') wirePoints(svg, c);
    }
  }
  function reset() { registry.clear(); tipHide(); }

  return { mileage, progression, course, wire, reset, seasonName, ms, DAY, wmd, year };
})();
