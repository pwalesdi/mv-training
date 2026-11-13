'use strict';
/* MV Training prototype - plain JS. Routes:
   #/calendar[/<season>][?m=YYYY-MM]   #/day/<date>[?a=<athlete key to highlight>]
   #/athletes                          #/athletes/<key>[?tab=calendar|history|mileage|races&season=&m=&cat=]
   #/workouts[/<combo>]     */

const main = document.getElementById('main');
const tip = document.getElementById('tip');
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const enc = encodeURIComponent;
const DOW = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
let META = null, ATHLETES = null, WTYPES = null;
let filter = 'varsity';
try { filter = localStorage.getItem('mv.filter') || 'varsity'; } catch (e) { /* storage blocked: default */ }

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status}: ${(await r.text()).replace(/<[^>]+>/g, ' ').slice(0, 200)}`);
  return r.json();
}

/* ---------- formatting ---------- */
function fmtTime(s) {
  if (s === null || s === undefined || s === '') return '';
  let t = Math.round(Number(s) * 10) / 10;
  if (!isFinite(t)) return '';
  const h = Math.floor(t / 3600); t = Math.round((t - h * 3600) * 10) / 10;
  const m = Math.floor(t / 60); const sec = Math.round((t - m * 60) * 10) / 10;
  let ss = sec.toFixed(1).replace(/\.0$/, '');
  if (!h && !m) return ss;
  if (sec < 10) ss = '0' + ss;
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}
function fmtDist(d) {
  if (d === null || d === undefined || d === '') return '';
  d = Number(d);
  if (Math.abs(d - 1725) < 1) return 'Lap';
  const laps = d / 1725;
  if (laps > 1 && Math.abs(laps - Math.round(laps)) < 0.001) return `${Math.round(laps)} laps`;
  return String(Math.round(d));
}
const utc = iso => { const [y, m, d] = iso.split('-').map(Number); return new Date(Date.UTC(y, m - 1, d)); };
const fmtDateLong = iso => utc(iso).toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
const fmtDateShort = iso => utc(iso).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', timeZone: 'UTC' });
const monthName = (ym, short) => { const [y, m] = ym.split('-').map(Number); return short ? MONTHS[m - 1].slice(0, 3) : `${MONTHS[m - 1]} ${y}`; };
const seasonLabel = s => `20${s.slice(0, 2)}-${s.slice(2)}`;
function monthRange(first, last) {
  const out = []; let [y, m] = first.split('-').map(Number); const [ly, lm] = last.split('-').map(Number);
  while (y < ly || (y === ly && m <= lm)) { out.push(`${y}-${String(m).padStart(2, '0')}`); m++; if (m > 12) { m = 1; y++; } }
  return out;
}
const nz = v => (v === null || v === undefined || v === '') ? '' : v;
// '1600 Meters' -> '1600m', '5,000 Meters' -> '5000m', '2.13 Miles' -> '2.13 mi'
const shortEvent = e => String(e || '').replace(/,/g, '').replace(/\s*Meters?$/i, 'm').replace(/\s*Miles?$/i, ' mi');
// link to an athlete's page; ctx {season, date} opens their calendar at that month
function athleteHref(key, ctx) {
  const p = new URLSearchParams();
  if (ctx && ctx.season) p.set('season', ctx.season);
  if (ctx && ctx.date) p.set('m', ctx.date.slice(0, 7));
  return `#/athletes/${enc(key)}${p.toString() ? '?' + p : ''}`;
}
// 'Doe, Jane' and 'Jane Doe' are the same spelling; only a real difference is worth showing
const nameKey = s => String(s || '').toLowerCase().replace(/\(.*?\)/g, ' ').replace(/[^a-z]+/g, ' ').trim().split(' ').sort().join(' ');

/* ---------- chips ---------- */
function chipHtml(c) {
  if (!c) return '';
  const inf = c.src === 'inferred';
  let title = inf ? 'Inferred from target pace vs race fitness - not one of Patrick’s labels'
    : (c.src === 'override' || c.override) ? 'Patrick’s decision (rules/line_overrides.csv)' : 'From Patrick’s label';
  if (c.proposed) title += ' · category mapping proposed by Claude, not confirmed yet';
  return `<span class="chip t-${esc(c.type)}${inf ? ' inferred' : ''}" title="${esc(title)}">${esc(c.label)}`
    + `${c.proposed ? ' <span class="tag">?</span>' : ''}${inf ? ' <span class="tag">inferred</span>' : ''}</span>`;
}
function legendHtml() {
  const cats = META.categories.map(c => chipHtml({ type: c.type, label: c.name, src: 'sheet' })).join('');
  return `<div class="legend">${cats}${chipHtml({ type: 'threshold', label: 'Threshold', src: 'inferred' })}
    <span><span class="badge double">2× T</span> double-threshold day</span>
    <span><span class="badge">Church</span> church loop</span>
    <span><span class="badge race">Race</span> race day</span></div>`;
}
function filterNote(res, season) {
  if (res.filter === 'varsity') {
    return `<details class="note small"><summary>Showing groups with at least one of the ${res.varsity_count} varsity athletes for ${seasonLabel(season)}. Switch to “All athletes” above for everyone.</summary><p>${esc(META.varsity_rule)}</p></details>`;
  }
  if (filter === 'varsity' && !res.varsity_available) {
    return `<p class="note small">No cross-country race data for ${seasonLabel(season)}, so there is no varsity list - showing all athletes.</p>`;
  }
  return '';
}

/* ---------- router ---------- */
function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const i = raw.indexOf('?');
  const path = i < 0 ? raw : raw.slice(0, i);
  const parts = path.split('/').filter(Boolean).map(p => { try { return decodeURIComponent(p); } catch (e) { return p; } });
  return { view: parts[0] || 'calendar', parts: parts.slice(1), q: new URLSearchParams(i < 0 ? '' : raw.slice(i + 1)) };
}
async function route() {
  const { view, parts, q } = parseHash();
  Charts.reset();
  document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('on', a.dataset.nav === (view === 'day' ? 'calendar' : view)));
  document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('on', b.dataset.filter === filter));
  try {
    if (!META) META = await api('/api/meta');
    if (view === 'day') await viewDay(parts[0], q);
    else if (view === 'athletes') await (parts[0] ? viewAthlete(parts[0], q) : viewAthletes(q));
    else if (view === 'workouts') await viewWorkouts(parts[0]);
    else await viewCalendar(parts[0] || META.default_season, q.get('m'));
  } catch (err) {
    main.innerHTML = `<p class="warn">Couldn’t load this view: ${esc(err.message)}</p>`;
  }
}
window.addEventListener('hashchange', route);
document.querySelectorAll('[data-filter]').forEach(b => b.addEventListener('click', () => {
  filter = b.dataset.filter;
  try { localStorage.setItem('mv.filter', filter); } catch (e) { /* ignore */ }
  route();
}));

/* ---------- calendar ---------- */
async function viewCalendar(season, month) {
  const cal = await api(`/api/calendar/${enc(season)}?f=${filter}`);
  const days = cal.days, dates = Object.keys(days).sort();
  const withData = new Set(dates.map(d => d.slice(0, 7)));
  let months = monthRange(cal.first_month, cal.last_month);
  if (dates.length) months = months.filter(m => m >= dates[0].slice(0, 7) && m <= dates[dates.length - 1].slice(0, 7));
  const opts = META.seasons.map(s => `<option value="${s.season}"${s.season === season ? ' selected' : ''}>${esc(s.label)}</option>`).join('');
  let html = `<div class="bar"><h1>Team calendar</h1><select id="season" aria-label="Season">${opts}</select>
    <nav class="months" aria-label="Months">${months.map(m => `<a class="${withData.has(m) ? '' : 'empty'}" href="#/calendar/${season}?m=${m}">${monthName(m, true)}</a>`).join('')}</nav></div>`;
  html += filterNote(cal, season) + legendHtml();
  for (const m of months) html += monthGrid(m, days);
  if (!months.length) html += '<p class="muted">No days in this season.</p>';
  main.innerHTML = html;
  document.getElementById('season').addEventListener('change', e => { location.hash = `#/calendar/${e.target.value}`; });
  const fall = `20${season.slice(0, 2)}-08`;
  const want = month || (withData.has(fall) ? fall : months[0]);
  const el = want && document.getElementById(`m-${want}`);
  if (el && want !== months[0]) el.scrollIntoView(); else window.scrollTo(0, 0);
}
function monthGrid(m, days, cell = dayCell) {
  const [y, mo] = m.split('-').map(Number);
  const lead = (new Date(Date.UTC(y, mo - 1, 1)).getUTCDay() + 6) % 7;
  const n = new Date(Date.UTC(y, mo, 0)).getUTCDate();
  let cells = '<div class="cell out"></div>'.repeat(lead);
  for (let d = 1; d <= n; d++) {
    const iso = `${m}-${String(d).padStart(2, '0')}`;
    cells += days[iso] ? cell(iso, d, days[iso]) : `<div class="cell"><div class="top-row"><span class="dnum">${d}</span></div></div>`;
  }
  return `<section class="month" id="m-${m}"><h2>${monthName(m)}</h2><div class="grid">${DOW.map(x => `<div class="dow">${x}</div>`).join('')}${cells}</div></section>`;
}
function dayCell(iso, d, info) {
  const b = [];
  if (info.double) b.push('<span class="badge double" title="Double-threshold day (Session 1 + Session 2)">2× T</span>');
  if (info.church) b.push('<span class="badge" title="Church loop (1725 m)">Church</span>');
  if (info.races.length) b.push('<span class="badge race">Race</span>');
  const MAX = 5;
  const chips = info.chips.slice(0, MAX).map(chipHtml).join('')
    + (info.chips.length > MAX ? `<span class="more">+${info.chips.length - MAX} more</span>` : '');
  const races = info.races.map(r => `<span class="racename" title="${esc(r.meet)} · ${r.n} athlete${r.n === 1 ? '' : 's'}">${esc(r.meet)}</span>`).join('');
  const quiet = info.practice && !info.groups ? `<span class="quiet">${info.hidden_groups ? 'no varsity groups' : 'no groups listed'}</span>`
    : info.groups && !info.chips.length ? `<span class="quiet">${info.groups} group${info.groups === 1 ? '' : 's'} · no type</span>` : '';
  const label = `${fmtDateLong(iso)}: ${info.chips.map(c => c.label + (c.src === 'inferred' ? ' (inferred)' : '')).join(', ') || 'no workout chips'}`;
  return `<a class="cell has" href="#/day/${iso}" aria-label="${esc(label)}"><div class="top-row"><span class="dnum">${d}</span>${b.join('')}</div><div class="chips">${chips}</div>${races}${quiet}</a>`;
}

/* ---------- day ---------- */
async function viewDay(date, q) {
  const want = q.get('a') || '';
  const day = await api(`/api/day/${enc(date)}?f=${filter}${want ? `&a=${enc(want)}` : ''}`);
  const ym = date.slice(0, 7), hl = day.highlight;
  const ctx = { season: day.season, date, hl: hl ? hl.key : '' };
  const step = d => `#/day/${d}${hl ? `?a=${enc(hl.key)}` : ''}`;
  const back = hl ? `<a href="${athleteHref(hl.key, ctx)}">← ${esc(hl.name)}’s calendar</a>` : `<a href="#/calendar/${day.season}?m=${ym}">← ${monthName(ym)}</a>`;
  let html = `<div class="bar">${back}
    ${day.prev ? `<a href="${step(day.prev)}">‹ ${fmtDateShort(day.prev)}</a>` : ''}
    ${day.next ? `<a href="${step(day.next)}">${fmtDateShort(day.next)} ›</a>` : ''}
    ${hl ? `<span class="muted small">(${esc(hl.name)}’s days)</span><a class="small" href="#/day/${date}">team view of this day</a>` : ''}</div>`;
  const badges = [day.double ? '<span class="badge double">2× T double-threshold day</span>' : '',
    day.church ? '<span class="badge">Church loop</span>' : '', day.races.length ? '<span class="badge race">Race day</span>' : ''].join(' ');
  html += `<div class="dayhead"><h1>${fmtDateLong(date)}</h1><span class="muted">${seasonLabel(day.season)}</span>${badges}</div>`;
  if (hl) html += `<p class="small"><span class="hlkey">${esc(hl.name)}</span> highlighted${day.filter === 'varsity' && !hl.varsity ? ' - shown with the varsity athletes even though they weren’t varsity this season' : ''}.</p>`;
  if (day.filter === 'varsity') html += `<p class="muted small">Varsity filter: only varsity athletes are listed. Switch to “All athletes” to see every group.</p>`;
  else if (filter === 'varsity' && !day.varsity_available) html += '<p class="note small">No varsity list for this season (no cross-country race data) - showing all athletes.</p>';
  if (day.races.length) html += `<h2>Races</h2>${racesTable(day.races, false, ctx)}`;
  if (!day.sessions.length) html += '<p class="muted">No workout sheet for this day.</p>';
  for (const s of day.sessions) html += sessionHtml(s, ctx);
  if (day.sessions.length) html += `<p class="keyrow"><span><u style="text-decoration-style:dotted">underlined</u> = group-timed split (same time as another athlete)</span><span>† = athlete’s own variation</span><span class="warn">red</span> = suspect split (over an hour)<span>Label line: solid = from your label, dashed = inferred from pace</span></p>`;
  main.innerHTML = html;
  const first = hl && main.querySelector('tr.hl');
  if (first) first.scrollIntoView({ block: 'center' }); else window.scrollTo(0, 0);
}
function racesTable(races, showDate, ctx = {}) {
  return `<table class="list"><thead><tr>${showDate ? '<th>Date</th>' : ''}<th>Athlete</th><th>Meet</th><th>Event</th><th>Race</th><th>Mark</th><th>Place</th></tr></thead><tbody>${
    races.map(r => `<tr${ctx.hl && r.key === ctx.hl ? ' class="hl"' : ''}>${showDate ? `<td>${fmtDateShort(r.date)}</td>` : ''}<td><a href="${athleteHref(r.key, { season: ctx.season, date: r.date })}">${esc(r.athlete)}</a></td><td>${esc(r.meet)}</td><td>${esc(r.event)}</td><td>${esc(r.race)}</td><td class="mono">${esc(r.mark)}</td><td>${esc(r.place)}</td></tr>`).join('')}</tbody></table>`;
}
function sessionHtml(s, ctx) {
  const name = s.session_no ? `Session ${s.session_no}` : 'Practice';
  const bits = [s.start_time, s.venue, s.weather].filter(Boolean).map(esc).join(' · ');
  let html = `<section class="session"><header><h3>${name}</h3><span class="muted">${bits}</span><span class="muted small">tab ${esc(s.tab)}</span>`;
  if (s.session_note) html += `<span class="small">${esc(s.session_note)}</span>`;
  if (s.date_conflict) html += `<span class="warn small" title="${esc(s.date_conflict)}">sheet title says “${esc(s.title)}” - tab date used</span>`;
  html += '</header>';
  if (!s.blocks.length) html += `<div class="block muted">${s.hidden_groups ? `${s.hidden_groups} group(s) without varsity athletes hidden.` : 'No groups on this sheet.'}</div>`;
  s.blocks.forEach(b => { html += blockHtml(b, ctx); });
  if (s.blocks.length && s.hidden_groups) html += `<div class="block muted small">${s.hidden_groups} more group(s) without varsity athletes - switch to “All athletes” to see them.</div>`;
  return html + '</section>';
}
function blockHtml(b, ctx) {
  let html = `<div class="block"><h3>Group ${b.block_no}</h3>`;
  if (b.labels.length) {
    html += `<ul class="labels">${b.labels.map(l => `<li>${chipHtml(l.chip)}<span class="text">${esc(l.text)}</span>${l.reps === 0 && b.columns.length ? ' <span class="muted small">(no rep columns matched this line)</span>' : ''}</li>`).join('')}</ul>`;
  } else {
    const why = b.type_source === 'inferred' ? ' - the types below are inferred from target pace, not your labels'
      : b.type_source === 'override' ? ' - type set in rules/line_overrides.csv' : b.columns.length ? ' - no type could be inferred' : '';
    html += `<p class="muted small">No label lines on this group${why}.</p>`;
  }
  const extra = b.chips.filter(c => c.src !== 'sheet');
  if (extra.length) html += `<div class="chips" style="margin:0 0 8px"><span class="muted small">Unlabeled reps:</span>${extra.map(chipHtml).join('')}</div>`;
  if (b.notes) html += `<p class="small"><span class="muted">Notes:</span> ${esc(b.notes)}</p>`;
  html += b.columns.length ? repTable(b, ctx) : noRepTable(b, ctx);
  return html + '</div>';
}
function athleteCell(a, ctx) {
  let s = `<a href="${athleteHref(a.key, ctx)}">${esc(a.name)}</a>`;
  if (a.varsity) s += ' <span class="badge v" title="Varsity this season">V</span>';
  if (a.match === 'unmatched') s += ' <span class="warn small" title="No Athletic.net match - written in the workbook as shown">(unmatched)</span>';
  else if (a.workbook_name && nameKey(a.workbook_name) !== nameKey(a.name)) s += ` <span class="muted small" title="As written in the workbook">${esc(a.workbook_name)}</span>`;
  if (a.status) s += ` <span class="badge">${esc(a.status)}</span>`;
  return s;
}
function repCell(r) {
  if (!r) return '<td></td>';
  const cls = [], title = [];
  let txt = '';
  if (r.status === 'timed') { txt = fmtTime(r.actual); }
  else if (r.status === 'skipped') { txt = r.raw || 'N/A'; cls.push('skip'); }
  else if (r.status === 'note') { txt = r.raw; cls.push('note'); }
  if (r.shared) { cls.push('shared'); title.push('group-timed: same split as another athlete'); }
  if (r.suspect) { cls.push('suspect'); title.push('suspect split (over an hour) - probably a fill error in the sheet'); }
  if (r.variant) { txt += '†'; title.push(`own variation: ${fmtDist(r.distance)} @ ${fmtTime(r.target)}`); }
  return `<td class="${cls.join(' ')}"${title.length ? ` title="${esc(title.join('; '))}"` : ''}>${esc(txt)}</td>`;
}
function laneCell(c) {
  const ch = c.chip;
  if (!ch) return '<th><span class="lane none"></span>—</th>';
  const inf = ch.src === 'inferred';
  const how = inf ? 'inferred from pace' : (c.how || '');
  return `<th title="${esc(`${ch.label} - ${how}`)}"><span class="lane t-${esc(ch.type)}${inf ? ' inferred' : ''}"></span><span class="small">${c.line ? `L${c.line}` : 'inf'}</span></th>`;
}
function repTable(b, ctx = {}) {
  const cols = b.columns;
  const rows = b.athletes.map(a => `<tr${ctx.hl && a.key === ctx.hl ? ' class="hl"' : ''}><td>${athleteCell(a, ctx)}</td>${cols.map(c => repCell(a.reps[c.rep_no])).join('')}<td>${esc(nz(a.cooldown))}</td><td>${esc(nz(a.total_vol))}</td><td class="notes">${esc(a.notes)}</td></tr>`).join('');
  return `<div class="tablewrap"><table class="reps"><thead>
    <tr><th>Rep</th>${cols.map(c => `<th>${esc(fmtDist(c.distance))}</th>`).join('')}<th>Cooldown</th><th>Total</th><th class="notes">Notes</th></tr>
    <tr class="sub"><th>Target</th>${cols.map(c => `<th>${esc(fmtTime(c.target))}</th>`).join('')}<th>mi</th><th>mi</th><th class="notes"></th></tr>
    <tr class="sub"><th>Label line</th>${cols.map(laneCell).join('')}<th></th><th></th><th class="notes"></th></tr></thead>
    <tbody>${rows || `<tr><td class="muted" colspan="${cols.length + 4}">No athletes listed.</td></tr>`}</tbody>
    <tfoot><tr class="sub"><th>Rest after</th>${cols.map(c => `<td class="muted">${esc(c.rest)}</td>`).join('')}<td></td><td></td><td class="notes"></td></tr></tfoot></table></div>`;
}
function noRepTable(b, ctx = {}) {
  if (!b.athletes.length) return '<p class="muted small">No athletes and no reps on this group.</p>';
  return `<p class="muted small">No rep columns on this group (cooldown / total volume only).</p><div class="tablewrap"><table class="reps"><thead><tr><th>Athlete</th><th>Cooldown mi</th><th>Total mi</th><th class="notes">Notes</th></tr></thead><tbody>${
    b.athletes.map(a => `<tr${ctx.hl && a.key === ctx.hl ? ' class="hl"' : ''}><td>${athleteCell(a, ctx)}</td><td>${esc(nz(a.cooldown))}</td><td>${esc(nz(a.total_vol))}</td><td class="notes">${esc(a.notes)}</td></tr>`).join('')}</tbody></table></div>`;
}

/* ---------- athletes ---------- */
async function viewAthletes(q) {
  if (!ATHLETES) ATHLETES = await api('/api/athletes');
  const season = q.get('season') || '';
  const opts = ['<option value="">All seasons</option>'].concat(META.seasons.map(s => `<option value="${s.season}"${s.season === season ? ' selected' : ''}>${esc(s.label)}</option>`)).join('');
  main.innerHTML = `<div class="bar"><h1>Athletes</h1><input type="search" id="q" placeholder="Search name" aria-label="Search athletes" autofocus><select id="season" aria-label="Season">${opts}</select><span class="muted small" id="count"></span></div>
    <table class="list"><thead><tr><th>Name</th><th>Seasons in workbooks / races</th><th>Varsity</th><th>Sessions</th><th>Races</th></tr></thead><tbody id="rows"></tbody></table>`;
  const draw = () => {
    const term = document.getElementById('q').value.trim().toLowerCase();
    const list = ATHLETES.filter(a => (!season || a.seasons.includes(season))
      && (filter === 'all' || (season ? a.varsity_seasons.includes(season) : a.varsity_seasons.length))
      && (!term || a.name.toLowerCase().includes(term)));
    document.getElementById('count').textContent = `${list.length} athlete${list.length === 1 ? '' : 's'}${filter === 'varsity' ? ' (varsity filter on)' : ''}`;
    document.getElementById('rows').innerHTML = list.map(a => `<tr><td><a href="#/athletes/${enc(a.key)}${season ? `?season=${season}` : ''}">${esc(a.name)}</a>${a.matched ? '' : ' <span class="warn small">(unmatched)</span>'}</td><td>${a.seasons.map(seasonLabel).join(', ')}</td><td>${a.varsity_seasons.map(seasonLabel).join(', ')}</td><td>${a.sessions}</td><td>${a.races}</td></tr>`).join('')
      || '<tr><td colspan="5" class="muted">No athletes match.</td></tr>';
  };
  document.getElementById('q').addEventListener('input', draw);
  document.getElementById('season').addEventListener('change', e => { location.hash = `#/athletes${e.target.value ? `?season=${e.target.value}` : ''}`; });
  draw();
}
const ATHLETE_TABS = [['calendar', 'Calendar'], ['history', 'History'], ['mileage', 'Mileage'], ['races', 'Races']];
function athleteUrl(key, params) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v && !(k === 'tab' && v === 'calendar')) p.set(k, v);
  return `#/athletes/${enc(key)}${p.toString() ? '?' + p : ''}`;
}
function athleteHead(a, key, tab, season) {
  const tabs = ATHLETE_TABS.map(([t, label]) => `<a href="${athleteUrl(key, { tab: t, season })}"${t === tab ? ' class="on" aria-current="page"' : ''}>${label}</a>`).join('');
  return `<div class="bar"><a href="#/athletes">← Athletes</a></div>
    <div class="dayhead"><h1>${esc(a.name)}</h1>${a.athlete_id ? `<span class="muted small">Athletic.net ${esc(a.athlete_id)}</span>` : '<span class="warn small">not matched to Athletic.net</span>'}
    ${a.varsity_seasons.map(s => `<span class="badge v">Varsity ${seasonLabel(s)}</span>`).join(' ')}</div>
    <nav class="tabs" aria-label="Athlete views">${tabs}</nav>`;
}
async function viewAthlete(key, q) {
  const tab = q.get('tab') || 'calendar';
  if (tab === 'history') return athleteHistory(key, q);
  if (tab === 'mileage') return athleteMileage(key, q);
  if (tab === 'races') return athleteRaces(key, q);
  return athleteCalendar(key, q);
}
async function athleteCalendar(key, q) {
  const cal = await api(`/api/athlete/calendar?key=${enc(key)}&season=${enc(q.get('season') || '')}`);
  const a = cal.athlete, season = cal.season, days = cal.days, dates = Object.keys(days).sort();
  const withData = new Set(dates.map(d => d.slice(0, 7)));
  const months = dates.length ? monthRange(dates[0].slice(0, 7), dates[dates.length - 1].slice(0, 7)) : [];
  const opts = a.seasons.map(s => `<option value="${s}"${s === season ? ' selected' : ''}>${seasonLabel(s)}</option>`).join('');
  const nT = dates.filter(d => days[d].practice).length, nR = dates.filter(d => days[d].races.length).length;
  let html = athleteHead(a, key, 'calendar', season);
  html += `<div class="bar"><select id="season" aria-label="Season">${opts}</select>
    <nav class="months" aria-label="Months">${months.map(m => `<a class="${withData.has(m) ? '' : 'empty'}" href="${athleteUrl(key, { season, m })}">${monthName(m, true)}</a>`).join('')}</nav>
    <span class="muted small">${nT} training day${nT === 1 ? '' : 's'} · ${nR} race day${nR === 1 ? '' : 's'} in ${seasonLabel(season)}</span></div>`;
  html += `<p class="muted small">Only the days ${esc(a.name)} is on a workout sheet or raced. Chips are the types of their own reps (dashed = inferred from pace). Click a day for the full day with their row highlighted.</p>`;
  html += legendHtml();
  for (const m of months) html += monthGrid(m, days, (iso, d, info) => athleteDayCell(iso, d, info, key));
  if (!months.length) html += `<p class="muted">No training or races in ${seasonLabel(season)}.</p>`;
  main.innerHTML = html;
  document.getElementById('season').addEventListener('change', e => { location.hash = athleteUrl(key, { season: e.target.value }); });
  const want = q.get('m'), el = want && document.getElementById(`m-${want}`);
  if (el && want !== months[0]) el.scrollIntoView(); else window.scrollTo(0, 0);
}
function athleteDayCell(iso, d, info, key) {
  const b = [];
  if (info.double) b.push('<span class="badge double" title="Did both sessions of a double-threshold day">2× T</span>');
  if (info.church) b.push('<span class="badge" title="Church loop (1725 m)">Church</span>');
  if (info.races.length) b.push('<span class="badge race">Race</span>');
  const MAX = 5;
  const chips = info.chips.slice(0, MAX).map(chipHtml).join('')
    + (info.chips.length > MAX ? `<span class="more">+${info.chips.length - MAX} more</span>` : '');
  const meets = [];  // one meet name, then each event + mark (prelims and finals, 1600 and 3200 ...)
  for (const r of info.races) {
    let m = meets.find(x => x.meet === r.meet);
    if (!m) meets.push(m = { meet: r.meet, rows: [] });
    m.rows.push(r);
  }
  const races = meets.map(m => `<span class="racename" title="${esc(m.meet)}">${esc(m.meet)}</span>${m.rows.map(r => {
    const t = [r.event, r.race, r.mark, r.place ? `place ${r.place}` : '', r.also ? 'also listed in the other (XC / track) export' : ''].filter(Boolean).join(' · ');
    return `<span class="raceres" title="${esc(t)}">${esc(shortEvent(r.event))} <b>${esc(r.mark)}</b></span>`;
  }).join('')}`).join('');
  const quiet = info.practice && !info.chips.length ? '<span class="quiet">practice · no type</span>' : '';
  const label = `${fmtDateLong(iso)}: ${[...info.chips.map(c => c.label + (c.src === 'inferred' ? ' (inferred)' : '')), ...info.races.map(r => `${r.meet} ${r.event} ${r.mark}`)].join(', ') || 'practice, no type'}`;
  return `<a class="cell has" href="#/day/${iso}?a=${enc(key)}" aria-label="${esc(label)}"><div class="top-row"><span class="dnum">${d}</span>${b.join('')}</div><div class="chips">${chips}</div>${races}${quiet}</a>`;
}
async function athleteHistory(key, q) {
  if (!WTYPES) WTYPES = await api('/api/workout-types');
  const season = q.get('season') || '', cat = q.get('cat') || '';
  const a = await api(`/api/athlete?key=${enc(key)}&season=${enc(season)}&cat=${enc(cat)}`);
  const sOpts = ['<option value="">All seasons</option>'].concat(a.seasons.map(s => `<option value="${s}"${s === season ? ' selected' : ''}>${seasonLabel(s)}</option>`)).join('');
  const cOpts = ['<option value="">All categories</option>'].concat(comboOptions().map(o => `<option value="${esc(o.combo)}"${o.combo === cat ? ' selected' : ''}>${esc(o.label)}</option>`)).join('');
  const sessions = a.items.filter(i => i.kind === 'session').length, races = a.items.filter(i => i.kind === 'race').length;
  let html = athleteHead(a, key, 'history', season) + `
    <div class="bar"><select id="season" aria-label="Season">${sOpts}</select><select id="cat" aria-label="Category">${cOpts}</select>
    <span class="muted small">${sessions} session${sessions === 1 ? '' : 's'} · ${races} race${races === 1 ? '' : 's'}${cat ? ' (races always shown)' : ''}</span></div>`;
  html += timeline(a.items, key);
  main.innerHTML = html;
  const go = () => {
    location.hash = athleteUrl(key, { tab: 'history', season: document.getElementById('season').value, cat: document.getElementById('cat').value });
  };
  document.getElementById('season').addEventListener('change', go);
  document.getElementById('cat').addEventListener('change', go);
  window.scrollTo(0, 0);
}
const chartWidth = () => Math.max(360, Math.min(1440, (main.clientWidth || 960) - 40 - 26));
const fmtMi = v => (v === null || v === undefined || v === '') ? '' : String(+Number(v).toFixed(2));
async function athleteMileage(key, q) {
  const res = await api(`/api/athlete/mileage?key=${enc(key)}`);
  const a = res.athlete, weeks = res.weeks;
  let html = athleteHead(a, key, 'mileage', q.get('season') || '');
  const span = res.all_weeks_span ? `Strava data so far covers ${fmtDateShort(res.all_weeks_span[0])}, ${res.all_weeks_span[0].slice(0, 4)} – ${fmtDateShort(res.all_weeks_span[1])}, ${res.all_weeks_span[1].slice(0, 4)} for ${res.n_athletes_with_mileage} athletes.` : 'No Strava mileage has been loaded yet.';
  if (!weeks.length) {
    html += `<p>${a.athlete_id ? `No Strava weekly mileage for ${esc(a.name)}.` : `${esc(a.name)} isn’t matched to an Athletic.net ID, and Strava mileage is matched through that ID (data/rules/strava_aliases.csv), so none can be shown.`}</p><p class="muted small">${span} Weeks without Strava data are left empty - the workbook volume is never used to fill them.</p>`;
    main.innerHTML = html;
    window.scrollTo(0, 0);
    return;
  }
  const first = weeks[0], last = weeks[weeks.length - 1];
  html += `<div class="chart"><h3>Weekly mileage</h3>
    <p class="muted small">Strava, Monday–Sunday weeks, ${weeks.length} week${weeks.length === 1 ? '' : 's'} from ${fmtDateShort(first.week_start)}, ${first.week_start.slice(0, 4)} to ${fmtDateShort(last.week_end)}, ${last.week_end.slice(0, 4)}. Hover (or focus and use ← →) for each week.</p>
    <div class="chartkey"><span><i class="k-bar"></i>miles in the week</span><span><i class="k-bar partial"></i>partial week</span><span><i class="k-gap"></i>no data (left empty)</span><span><i class="k-season"></i>season starts 1 July</span></div>
    ${Charts.mileage(weeks, chartWidth())}</div>`;
  html += mileageSeasons(weeks) + mileageWeeks(weeks);
  html += `<p class="muted small">${span} Weeks without Strava data stay empty - the workbook volume is never used to fill them.</p>`;
  main.innerHTML = html;
  Charts.wire(main);
  window.scrollTo(0, 0);
}
function mileageSeasons(weeks) {
  const by = new Map();
  for (const w of weeks) {
    const s = Charts.seasonName(Charts.ms(w.week_start));
    if (!by.has(s)) by.set(s, []);
    by.get(s).push(w);
  }
  const rows = [...by.entries()].reverse().map(([s, ws]) => {
    const full = ws.filter(w => !w.partial), total = ws.reduce((t, w) => t + Number(w.miles), 0);
    const avg = full.length ? full.reduce((t, w) => t + Number(w.miles), 0) / full.length : null;
    const peak = full.reduce((p, w) => (!p || Number(w.miles) > Number(p.miles) ? w : p), null);
    return `<tr><td>${esc(s)}</td><td class="num">${ws.length}</td><td class="num">${ws.length - full.length}</td><td class="num">${total.toFixed(1)}</td>
      <td class="num">${avg === null ? '–' : avg.toFixed(1)}</td><td class="num">${peak ? esc(fmtMi(peak.miles)) : '–'}</td><td>${peak ? `${fmtDateShort(peak.week_start)}, ${peak.week_start.slice(0, 4)}` : ''}</td></tr>`;
  }).join('');
  return `<h3 class="sub">By season</h3><table class="list mileage-seasons"><thead><tr><th>Season</th><th class="num">Weeks with data</th><th class="num">Partial weeks</th><th class="num">Total mi</th><th class="num">Avg full week</th><th class="num">Peak full week</th><th>Peak week starts</th></tr></thead><tbody>${rows}</tbody></table>
    <p class="muted small">Seasons run 1 July - 30 June; a week counts in the season it starts in. Averages and peaks use full weeks only.</p>`;
}
function mileageWeeks(weeks) {
  const DAY = Charts.DAY, out = [];
  for (let i = weeks.length - 1; i >= 0; i--) {
    const w = weeks[i];
    out.push(`<tr data-week="${esc(w.week_start)}"><td class="nowrap">${fmtDateShort(w.week_start)} – ${fmtDateShort(w.week_end)}, ${w.week_end.slice(0, 4)}</td><td class="num">${esc(fmtMi(w.miles))}</td><td>${w.partial ? `partial${w.note ? ': ' + esc(w.note) : ''}` : esc(w.note)}</td><td class="muted small">${esc(w.source)}</td></tr>`);
    if (i > 0) {  // the weeks missing between this one and the one before it
      const end = Charts.ms(weeks[i - 1].week_start) + 7 * DAY, next = Charts.ms(w.week_start);
      const n = Math.round((next - end) / (7 * DAY));
      if (next - end >= 6.5 * DAY) {
        const a = new Date(end).toISOString().slice(0, 10), b = new Date(next - DAY).toISOString().slice(0, 10);
        out.push(`<tr class="gaprow"><td class="nowrap">${fmtDateShort(a)} – ${fmtDateShort(b)}, ${b.slice(0, 4)}</td><td class="num">–</td><td>no data (${n} week${n === 1 ? '' : 's'})</td><td></td></tr>`);
      }
    }
  }
  return `<details class="weeks"><summary>Every week (${weeks.length}) - newest first</summary><table class="list"><thead><tr><th>Week (Mon–Sun)</th><th class="num">Miles</th><th>Note</th><th>File</th></tr></thead><tbody>${out.join('')}</tbody></table></details>`;
}
/* ---------- athlete races ---------- */
const longDate = iso => `${fmtDateShort(iso)}, ${iso.slice(0, 4)}`;
const secsText = v => `${fmtTime(v)}${Math.abs(v) < 60 ? ' s' : ''}`;
function deltaText(d) {
  if (d === null || d === undefined) return '';
  if (Math.abs(d) < 0.05) return 'same time';
  return d < 0 ? `${secsText(-d)} faster` : `${secsText(d)} slower`;
}
async function athleteRaces(key, q) {
  const res = await api(`/api/athlete/races?key=${enc(key)}`);
  const a = res.athlete, cov = res.coverage || {};
  const covText = Object.entries(cov).map(([k, [f, l]]) => `${k === 'TF' ? 'track' : 'cross country'} ${longDate(f)} – ${longDate(l)}`).join('; ');
  let html = athleteHead(a, key, 'races', q.get('season') || '');
  html += `<p class="muted small">Race times as Athletic.net has them - nothing here converts between events or courses. The exports cover ${esc(covText)}.</p>`;
  html += '<h2>Track</h2>' + trackSection(res.track) + '<h2>Cross country</h2>' + xcSection(res);
  main.innerHTML = html;
  Charts.wire(main);
  window.scrollTo(0, 0);
}
function trackSection(track) {
  if (!track.length) return '<p class="muted">No track results.</p>';
  const timed = track.flatMap(e => e.results.filter(r => r.seconds));
  if (!timed.length) return `<p class="muted">No timed track results (${track.reduce((n, e) => n + e.results.length, 0)} listed without a mark).</p>${trackTable(track)}`;
  const ts = timed.map(r => Charts.ms(r.date)), DAY = Charts.DAY;
  let t0 = Math.min(...ts), t1 = Math.max(...ts);
  const padT = Math.max(20 * DAY, (t1 - t0) * 0.03);
  t0 -= padT; t1 += padT;
  let html = `<p class="muted small">One panel per event on the same time axis; faster is higher. The line breaks between seasons and across gaps of more than three months.</p>
    <div class="chartkey"><span><i class="k-dot pr"></i>PR when it was run (faster than every earlier race in that event)</span><span><i class="k-dot"></i>other race</span><span><b>PR 4:30.0</b> lifetime PR</span></div>`;
  for (const e of track) {
    const pts = e.results.filter(r => r.seconds).map(r => ({
      t: Charts.ms(r.date), seconds: r.seconds, season: r.season,
      cls: r.best ? 'pr best' : r.pr ? 'pr' : '', label: r.best ? `PR ${r.mark}` : '',
      lines: [[r.mark, 'tv'], [`${e.event} · ${Charts.wmd(Charts.ms(r.date))}, ${r.date.slice(0, 4)}`], [r.meet],
        [[r.race, r.place ? `place ${r.place}` : ''].filter(Boolean).join(' · ')],
        ...(r.best ? [['Lifetime PR', 'tn']] : r.pr ? [['PR at the time', 'tn']] : []),
        ...(r.also ? [['also in the XC export', 'tn']] : [])].filter(l => l[0]),
    }));
    const n = e.results.length, untimed = n - pts.length;
    html += `<div class="chart track" data-event="${esc(e.event)}"><h3>${esc(e.event)} <span class="muted small">${n} race${n === 1 ? '' : 's'}${untimed ? ` (${untimed} without a time)` : ''}${e.best ? ` · PR ${esc(e.best.mark)}, ${esc(longDate(e.best.date))}, ${esc(e.best.meet)}` : ''}</span></h3>
      ${pts.length ? Charts.progression(pts, { t0, t1, width: chartWidth(), event: e.event }) : '<p class="muted small">No timed results.</p>'}</div>`;
  }
  return html + trackTable(track);
}
function trackTable(track) {
  const rows = track.flatMap(e => e.results.map(r => `<tr data-event="${esc(e.event)}" data-secs="${r.seconds ?? ''}"><td>${esc(e.event)}</td><td class="nowrap">${esc(longDate(r.date))}</td><td>${esc(r.meet)}</td><td>${esc(r.race)}</td><td class="num mono">${esc(r.mark)}</td><td class="num">${esc(r.place)}</td><td>${r.best ? '<b>lifetime PR</b>' : r.pr ? 'PR' : ''}${r.also ? ' <span class="muted small">also in XC export</span>' : ''}</td></tr>`)).join('');
  const n = track.reduce((t, e) => t + e.results.length, 0);
  return `<details class="weeks"><summary>All track results (${n}) - by event, oldest first</summary><table class="list"><thead><tr><th>Event</th><th>Date</th><th>Meet</th><th>Race</th><th class="num">Mark</th><th class="num">Place</th><th>PR</th></tr></thead><tbody>${rows}</tbody></table></details>`;
}
function xcSection(res) {
  if (!res.xc.length) return '<p class="muted">No cross-country results.</p>';
  let html = '<h3 class="sub">Same course, different years</h3><p class="muted small">Grouped by meet <b>and</b> distance: when a meet changed its distance (Lowell / SF Invitational) the years aren’t compared. Faster is higher.</p>';
  if (!res.courses.length) html += '<p class="muted">No course run more than once.</p>';
  else html += `<div class="courses">${res.courses.map(courseCard).join('')}</div>`;
  const rows = res.xc.map(r => `<tr data-secs="${r.seconds ?? ''}"><td class="nowrap">${esc(longDate(r.date))}</td><td>${esc(r.meet)}</td><td class="nowrap">${esc(r.event)}</td><td>${esc(r.race)}</td><td class="num mono">${esc(r.mark)}</td><td class="num">${esc(r.place)}</td><td>${r.varsity_race ? '<span class="badge v">varsity race</span> ' : ''}${r.course ? `<a class="small" href="#" data-course="${esc(r.course)}">same course ↑</a> ` : ''}${r.also ? '<span class="muted small">also in track export</span>' : ''}</td></tr>`).join('');
  return html + `<h3 class="sub">Every cross-country race (${res.xc.length}) - newest first</h3><table class="list xc"><thead><tr><th>Date</th><th>Meet</th><th>Distance</th><th>Race</th><th class="num">Time</th><th class="num">Place</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
}
function courseCard(c) {
  const years = c.results.map(r => r.date.slice(0, 4)), dupYear = new Set(years).size < years.length;
  const pts = c.results.filter(r => r.seconds).map(r => ({
    t: Charts.ms(r.date), seconds: r.seconds, cls: 'solid', label: '',
    xlabel: dupYear ? `${MONTHS[+r.date.slice(5, 7) - 1].slice(0, 3)} ’${r.date.slice(2, 4)}` : r.date.slice(0, 4),
    lines: [[r.mark, 'tv'], [`${Charts.wmd(Charts.ms(r.date))}, ${r.date.slice(0, 4)}`], [[r.race, r.place ? `place ${r.place}` : ''].filter(Boolean).join(' · ')],
      ...(r.delta !== null ? [[`${deltaText(r.delta)} than the time before on this course`, 'tn']] : [])].filter(l => l[0]),
  }));
  const rows = c.results.map(r => `<tr data-secs="${r.seconds ?? ''}"><td>${esc(r.date.slice(0, 4))}</td><td class="nowrap">${esc(fmtDateShort(r.date))}</td><td>${esc(r.race)}</td><td class="num mono">${esc(r.mark)}</td><td class="num">${esc(r.place)}</td><td class="num nowrap">${esc(deltaText(r.delta))}</td></tr>`).join('');
  // the card's own width in the .courses grid (auto-fill, min 460px, 12px gap), so the chart isn't stretched
  const avail = (main.clientWidth || 960) - 40, cols = Math.max(1, Math.floor((avail + 12) / 472));
  const w = Math.floor((avail - 12 * (cols - 1)) / cols) - 26;
  return `<div class="chart course" data-course="${esc(c.key)}"><h3>${esc(c.meet)} <span class="muted">· ${esc(c.event)}</span></h3>
    ${pts.length > 1 ? Charts.course(pts, w) : ''}
    <table class="list compact"><thead><tr><th>Year</th><th>Date</th><th>Race</th><th class="num">Time</th><th class="num">Place</th><th class="num">vs time before</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}
document.addEventListener('click', e => {  // "same course" links in the XC list scroll to that course's card
  const a = e.target.closest && e.target.closest('a[data-course]');
  if (!a) return;
  e.preventDefault();
  const card = [...document.querySelectorAll('.chart.course')].find(c => c.dataset.course === a.dataset.course);
  if (card) { card.scrollIntoView({ block: 'center' }); card.classList.add('flash'); setTimeout(() => card.classList.remove('flash'), 1200); }
});
function comboOptions() {  // built by the server, counts are distinct groups
  return WTYPES.map(w => ({ combo: w.combo, label: w.label, n: w.blocks }));
}
function timeline(items, key) {
  if (!items.length) return '<p class="muted">Nothing for this selection.</p>';
  let html = '', cur = null;
  for (const it of items) {
    if (it.season !== cur) { if (cur !== null) html += '</ul>'; cur = it.season; html += `<div class="seasonhead">${seasonLabel(cur)}</div><ul class="tl">`; }
    if (it.kind === 'race') {
      html += `<li><div class="when">${fmtDateShort(it.date)}<div class="small muted">${esc(it.race_kind)} race</div></div><div><span class="race">${esc(it.meet)}</span>
        <span class="muted">· ${esc(it.event)} · ${esc(it.race)}</span> — <b class="mono">${esc(it.mark)}</b>${it.place ? ` <span class="muted">place ${esc(it.place)}</span>` : ''}${it.varsity_race ? ' <span class="badge v">varsity race</span>' : ''}</div></li>`;
    } else {
      const reps = it.reps.map(r => {
        const v = r.status === 'timed' ? fmtTime(r.actual) : r.status === 'blank' ? '–' : (r.raw || r.status);
        const t = [`target ${fmtTime(r.target) || '-'}`, r.chip ? `${r.chip.label}${r.chip.src === 'inferred' ? ' (inferred)' : ''}` : '', r.shared ? 'group-timed' : '', r.suspect ? 'suspect split' : ''].filter(Boolean).join(' · ');
        return `<span title="${esc(t)}"><span class="d">${esc(fmtDist(r.distance))}</span> ${esc(v)}</span>`;
      }).join('');
      const vol = [it.cooldown ? `cooldown ${it.cooldown} mi` : '', it.total_vol ? `total ${it.total_vol} mi` : ''].filter(Boolean).join(' · ');
      html += `<li><div class="when"><a href="#/day/${it.date}?a=${enc(key)}">${fmtDateShort(it.date)}</a><div class="small muted">${it.session_no ? `Session ${it.session_no} · ` : ''}group ${esc(it.block_no)}</div></div>
        <div><div class="chips">${it.chips.map(chipHtml).join('')}</div>${it.labels.length ? `<div class="mono muted">${it.labels.map(esc).join('<br>')}</div>` : ''}
        ${reps ? `<div class="splits">${reps}</div>` : ''}${vol || it.notes ? `<div class="small muted">${esc(vol)}${it.notes ? ` · ${esc(it.notes)}` : ''}</div>` : ''}</div></li>`;
    }
  }
  return html + '</ul>';
}
/* ---------- workouts ---------- */
function groupNames(r) {  // every athlete in the group links to their calendar at that month
  const link = g => `<a href="${athleteHref(g.key, { season: r.season, date: r.date })}">${esc(g.name)}</a>`;
  const n = r.group.length, head = r.group.slice(0, 3).map(link).join(', ');
  const rest = n > 3 ? `<details class="names"><summary>+${n - 3} more</summary>${r.group.slice(3).map(link).join(', ')}</details>` : '';
  return `${n} athlete${n === 1 ? '' : 's'}<div class="small">${head}</div>${rest}`;
}
async function viewWorkouts(combo) {
  if (!WTYPES) WTYPES = await api('/api/workout-types');
  const opts = comboOptions();
  combo = combo || (opts.find(o => o.combo === 'threshold|DT|DT-AM') || opts[0] || {}).combo || '';
  const rows = combo ? await api(`/api/workouts?combo=${enc(combo)}&f=${filter}`) : [];
  const cur = opts.find(o => o.combo === combo);
  const picker = opts.map(o => `<a href="#/workouts/${enc(o.combo)}" class="${o.combo === combo ? 'on' : ''}"><span>${esc(o.label)}</span><span class="count">${o.n}</span></a>`).join('');
  const targets = ts => ts.map(t => `${t.n} × ${fmtDist(t.distance)}${t.min ? ` @ ${fmtTime(t.min)}${t.max !== t.min ? '–' + fmtTime(t.max) : ''}` : ''}`).join(' · ');
  const body = rows.map(r => `<tr><td class="nowrap"><a href="#/day/${r.date}">${fmtDateShort(r.date)}</a><div class="small muted">${seasonLabel(r.season)}</div></td>
    <td>${r.session_no ? `Session ${r.session_no}<br>` : ''}group ${r.block_no}<div class="small muted">${esc(r.venue)}</div></td>
    <td><div class="chips">${r.chips.map(chipHtml).join('')}</div>${r.labels.map(l => `<div class="mono">${esc(l)}</div>`).join('') || '<div class="small muted">no label line - inferred from pace</div>'}</td>
    <td class="mono">${esc(targets(r.targets))}</td>
    <td>${groupNames(r)}</td></tr>`).join('');
  main.innerHTML = `<div class="bar"><h1>Workouts by type</h1><span class="muted small">Every group that did this type, newest first. Counts on the left are all athletes; the list follows the filter above.</span></div>
    <div class="cols"><nav class="picker" aria-label="Workout types">${picker}</nav><div>
    <h2 style="margin-top:0">${esc(cur ? cur.label : combo)} <span class="muted small">${rows.length} group${rows.length === 1 ? '' : 's'}</span></h2>
    <table class="list"><thead><tr><th>Date</th><th>Session</th><th>Label (as written)</th><th>Key targets</th><th>Group</th></tr></thead><tbody>${body || '<tr><td colspan="5" class="muted">None for this filter.</td></tr>'}</tbody></table></div></div>`;
  window.scrollTo(0, 0);
}

/* ---------- tooltip for chart marks ---------- */
document.addEventListener('mousemove', e => {
  if (e.target.closest && e.target.closest('svg[data-chart]')) return;  // charts.js drives the tip there
  const t = e.target.closest && e.target.closest('[data-tip]');
  if (!t) { tip.hidden = true; return; }
  tip.textContent = t.getAttribute('data-tip');
  tip.hidden = false;
  tip.style.left = `${Math.min(e.clientX + 12, window.innerWidth - 290)}px`;
  tip.style.top = `${e.clientY + 14}px`;
});

route();
