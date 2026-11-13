'use strict';
/* MV Training prototype - plain JS. Routes:
   #/calendar[/<season>][?m=YYYY-MM]   #/day/<date>
   #/athletes[/<key>][?season=&cat=]   #/workouts[/<combo>]     */

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
  document.querySelectorAll('[data-nav]').forEach(a => a.classList.toggle('on', a.dataset.nav === (view === 'day' ? 'calendar' : view)));
  document.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('on', b.dataset.filter === filter));
  try {
    if (!META) META = await api('/api/meta');
    if (view === 'day') await viewDay(parts[0]);
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
function monthGrid(m, days) {
  const [y, mo] = m.split('-').map(Number);
  const lead = (new Date(Date.UTC(y, mo - 1, 1)).getUTCDay() + 6) % 7;
  const n = new Date(Date.UTC(y, mo, 0)).getUTCDate();
  let cells = '<div class="cell out"></div>'.repeat(lead);
  for (let d = 1; d <= n; d++) {
    const iso = `${m}-${String(d).padStart(2, '0')}`;
    cells += days[iso] ? dayCell(iso, d, days[iso]) : `<div class="cell"><div class="top-row"><span class="dnum">${d}</span></div></div>`;
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
async function viewDay(date) {
  const day = await api(`/api/day/${enc(date)}?f=${filter}`);
  const ym = date.slice(0, 7);
  let html = `<div class="bar"><a href="#/calendar/${day.season}?m=${ym}">← ${monthName(ym)}</a>
    ${day.prev ? `<a href="#/day/${day.prev}">‹ ${fmtDateShort(day.prev)}</a>` : ''}
    ${day.next ? `<a href="#/day/${day.next}">${fmtDateShort(day.next)} ›</a>` : ''}</div>`;
  const badges = [day.double ? '<span class="badge double">2× T double-threshold day</span>' : '',
    day.church ? '<span class="badge">Church loop</span>' : '', day.races.length ? '<span class="badge race">Race day</span>' : ''].join(' ');
  html += `<div class="dayhead"><h1>${fmtDateLong(date)}</h1><span class="muted">${seasonLabel(day.season)}</span>${badges}</div>`;
  if (day.filter === 'varsity') html += `<p class="muted small">Varsity filter: only varsity athletes are listed. Switch to “All athletes” to see every group.</p>`;
  else if (filter === 'varsity' && !day.varsity_available) html += '<p class="note small">No varsity list for this season (no cross-country race data) - showing all athletes.</p>';
  if (day.races.length) html += `<h2>Races</h2>${racesTable(day.races)}`;
  if (!day.sessions.length) html += '<p class="muted">No workout sheet for this day.</p>';
  for (const s of day.sessions) html += sessionHtml(s);
  if (day.sessions.length) html += `<p class="keyrow"><span><u style="text-decoration-style:dotted">underlined</u> = group-timed split (same time as another athlete)</span><span>† = athlete’s own variation</span><span class="warn">red</span> = suspect split (over an hour)<span>Label line: solid = from your label, dashed = inferred from pace</span></p>`;
  main.innerHTML = html;
  window.scrollTo(0, 0);
}
function racesTable(races, showDate) {
  return `<table class="list"><thead><tr>${showDate ? '<th>Date</th>' : ''}<th>Athlete</th><th>Meet</th><th>Event</th><th>Race</th><th>Mark</th><th>Place</th></tr></thead><tbody>${
    races.map(r => `<tr>${showDate ? `<td>${fmtDateShort(r.date)}</td>` : ''}<td><a href="#/athletes/${enc(r.key)}">${esc(r.athlete)}</a></td><td>${esc(r.meet)}</td><td>${esc(r.event)}</td><td>${esc(r.race)}</td><td class="mono">${esc(r.mark)}</td><td>${esc(r.place)}</td></tr>`).join('')}</tbody></table>`;
}
function sessionHtml(s) {
  const name = s.session_no ? `Session ${s.session_no}` : 'Practice';
  const bits = [s.start_time, s.venue, s.weather].filter(Boolean).map(esc).join(' · ');
  let html = `<section class="session"><header><h3>${name}</h3><span class="muted">${bits}</span><span class="muted small">tab ${esc(s.tab)}</span>`;
  if (s.session_note) html += `<span class="small">${esc(s.session_note)}</span>`;
  if (s.date_conflict) html += `<span class="warn small" title="${esc(s.date_conflict)}">sheet title says “${esc(s.title)}” - tab date used</span>`;
  html += '</header>';
  if (!s.blocks.length) html += `<div class="block muted">${s.hidden_groups ? `${s.hidden_groups} group(s) without varsity athletes hidden.` : 'No groups on this sheet.'}</div>`;
  s.blocks.forEach(b => { html += blockHtml(b); });
  if (s.blocks.length && s.hidden_groups) html += `<div class="block muted small">${s.hidden_groups} more group(s) without varsity athletes - switch to “All athletes” to see them.</div>`;
  return html + '</section>';
}
function blockHtml(b) {
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
  html += b.columns.length ? repTable(b) : noRepTable(b);
  return html + '</div>';
}
function athleteCell(a) {
  let s = `<a href="#/athletes/${enc(a.key)}">${esc(a.name)}</a>`;
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
function repTable(b) {
  const cols = b.columns;
  const rows = b.athletes.map(a => `<tr><td>${athleteCell(a)}</td>${cols.map(c => repCell(a.reps[c.rep_no])).join('')}<td>${esc(nz(a.cooldown))}</td><td>${esc(nz(a.total_vol))}</td><td class="notes">${esc(a.notes)}</td></tr>`).join('');
  return `<div class="tablewrap"><table class="reps"><thead>
    <tr><th>Rep</th>${cols.map(c => `<th>${esc(fmtDist(c.distance))}</th>`).join('')}<th>Cooldown</th><th>Total</th><th class="notes">Notes</th></tr>
    <tr class="sub"><th>Target</th>${cols.map(c => `<th>${esc(fmtTime(c.target))}</th>`).join('')}<th>mi</th><th>mi</th><th class="notes"></th></tr>
    <tr class="sub"><th>Label line</th>${cols.map(laneCell).join('')}<th></th><th></th><th class="notes"></th></tr></thead>
    <tbody>${rows || `<tr><td class="muted" colspan="${cols.length + 4}">No athletes listed.</td></tr>`}</tbody>
    <tfoot><tr class="sub"><th>Rest after</th>${cols.map(c => `<td class="muted">${esc(c.rest)}</td>`).join('')}<td></td><td></td><td class="notes"></td></tr></tfoot></table></div>`;
}
function noRepTable(b) {
  if (!b.athletes.length) return '<p class="muted small">No athletes and no reps on this group.</p>';
  return `<p class="muted small">No rep columns on this group (cooldown / total volume only).</p><div class="tablewrap"><table class="reps"><thead><tr><th>Athlete</th><th>Cooldown mi</th><th>Total mi</th><th class="notes">Notes</th></tr></thead><tbody>${
    b.athletes.map(a => `<tr><td>${athleteCell(a)}</td><td>${esc(nz(a.cooldown))}</td><td>${esc(nz(a.total_vol))}</td><td class="notes">${esc(a.notes)}</td></tr>`).join('')}</tbody></table></div>`;
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
async function viewAthlete(key, q) {
  if (!WTYPES) WTYPES = await api('/api/workout-types');
  const season = q.get('season') || '', cat = q.get('cat') || '';
  const a = await api(`/api/athlete?key=${enc(key)}&season=${enc(season)}&cat=${enc(cat)}`);
  const sOpts = ['<option value="">All seasons</option>'].concat(a.seasons.map(s => `<option value="${s}"${s === season ? ' selected' : ''}>${seasonLabel(s)}</option>`)).join('');
  const cOpts = ['<option value="">All categories</option>'].concat(comboOptions().map(o => `<option value="${esc(o.combo)}"${o.combo === cat ? ' selected' : ''}>${esc(o.label)}</option>`)).join('');
  const sessions = a.items.filter(i => i.kind === 'session').length, races = a.items.filter(i => i.kind === 'race').length;
  let html = `<div class="bar"><a href="#/athletes">← Athletes</a></div>
    <div class="dayhead"><h1>${esc(a.name)}</h1>${a.athlete_id ? `<span class="muted small">Athletic.net ${esc(a.athlete_id)}</span>` : '<span class="warn small">not matched to Athletic.net</span>'}
    ${a.varsity_seasons.map(s => `<span class="badge v">Varsity ${seasonLabel(s)}</span>`).join(' ')}</div>
    <div class="bar"><select id="season" aria-label="Season">${sOpts}</select><select id="cat" aria-label="Category">${cOpts}</select>
    <span class="muted small">${sessions} session${sessions === 1 ? '' : 's'} · ${races} race${races === 1 ? '' : 's'}${cat ? ' (races always shown)' : ''}</span></div>`;
  if (a.mileage.length) html += mileageChart(a.mileage);
  html += timeline(a.items);
  main.innerHTML = html;
  const go = () => {
    const s = document.getElementById('season').value, c = document.getElementById('cat').value;
    const qs = new URLSearchParams(); if (s) qs.set('season', s); if (c) qs.set('cat', c);
    location.hash = `#/athletes/${enc(key)}${qs.toString() ? '?' + qs : ''}`;
  };
  document.getElementById('season').addEventListener('change', go);
  document.getElementById('cat').addEventListener('change', go);
  window.scrollTo(0, 0);
}
function comboOptions() {  // built by the server, counts are distinct groups
  return WTYPES.map(w => ({ combo: w.combo, label: w.label, n: w.blocks }));
}
function timeline(items) {
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
      html += `<li><div class="when"><a href="#/day/${it.date}">${fmtDateShort(it.date)}</a><div class="small muted">${it.session_no ? `Session ${it.session_no} · ` : ''}group ${esc(it.block_no)}</div></div>
        <div><div class="chips">${it.chips.map(chipHtml).join('')}</div>${it.labels.length ? `<div class="mono muted">${it.labels.map(esc).join('<br>')}</div>` : ''}
        ${reps ? `<div class="splits">${reps}</div>` : ''}${vol || it.notes ? `<div class="small muted">${esc(vol)}${it.notes ? ` · ${esc(it.notes)}` : ''}</div>` : ''}</div></li>`;
    }
  }
  return html + '</ul>';
}
function mileageChart(rows) {
  // drawn at the container's pixel width so axis text stays 11px instead of scaling with the viewBox
  const W = Math.max(420, Math.min(1400, (main.clientWidth || 900) - 60)), H = 170, L = 34, R = 8, T = 12, B = 24;
  const max = Math.max(10, ...rows.map(r => r.miles));
  const top = Math.ceil(max / 10) * 10;
  const y = v => T + (H - T - B) * (1 - v / top);
  const bw = (W - L - R) / rows.length;
  const grid = [0, top / 2, top].map(v => `<g class="grid"><line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/></g><g class="axis"><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${v}</text></g>`).join('');
  const bars = rows.map((r, i) => {
    const x = L + i * bw + 1, w = Math.max(2, bw - 2), yy = y(r.miles), base = y(0), rad = Math.min(4, w / 2, base - yy);
    const d = `M${x},${base}V${yy + rad}Q${x},${yy} ${x + rad},${yy}H${x + w - rad}Q${x + w},${yy} ${x + w},${yy + rad}V${base}Z`;
    const tipText = `Week of ${fmtDateShort(r.week_start)}: ${r.miles} mi${r.partial ? ` (partial week${r.note ? ': ' + r.note : ''})` : ''}`;
    const lbl = i % Math.ceil(rows.length / 10) === 0 ? `<text x="${x + w / 2}" y="${H - 6}" text-anchor="middle">${esc(fmtDateShort(r.week_start).replace(/^\w+, /, ''))}</text>` : '';
    return `<path class="bar${r.partial ? ' partial' : ''}" d="${d}" data-tip="${esc(tipText)}"/><rect x="${L + i * bw}" y="${T}" width="${bw}" height="${H - T - B}" fill="transparent" data-tip="${esc(tipText)}"/><g class="axis">${lbl}</g>`;
  }).join('');
  const table = `<details><summary>Show as table</summary><table class="list"><thead><tr><th>Week</th><th>Miles</th><th>Note</th></tr></thead><tbody>${
    rows.map(r => `<tr><td>${esc(r.week_start)} – ${esc(r.week_end)}</td><td>${esc(r.miles)}</td><td>${r.partial ? `partial${r.note ? ': ' + esc(r.note) : ''}` : ''}</td></tr>`).join('')}</tbody></table></details>`;
  return `<div class="chart"><h3>Weekly mileage (Strava)</h3><p class="muted small">Dashed outline = partial week.</p><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Weekly mileage bar chart">${grid}${bars}</svg>${table}</div>`;
}

/* ---------- workouts ---------- */
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
    <td title="${esc(r.group.join(', '))}">${r.group.length} athlete${r.group.length === 1 ? '' : 's'}<div class="small muted">${esc(r.group.slice(0, 3).join(', '))}${r.group.length > 3 ? '…' : ''}</div></td></tr>`).join('');
  main.innerHTML = `<div class="bar"><h1>Workouts by type</h1><span class="muted small">Every group that did this type, newest first. Counts on the left are all athletes; the list follows the filter above.</span></div>
    <div class="cols"><nav class="picker" aria-label="Workout types">${picker}</nav><div>
    <h2 style="margin-top:0">${esc(cur ? cur.label : combo)} <span class="muted small">${rows.length} group${rows.length === 1 ? '' : 's'}</span></h2>
    <table class="list"><thead><tr><th>Date</th><th>Session</th><th>Label (as written)</th><th>Key targets</th><th>Group</th></tr></thead><tbody>${body || '<tr><td colspan="5" class="muted">None for this filter.</td></tr>'}</tbody></table></div></div>`;
  window.scrollTo(0, 0);
}

/* ---------- tooltip for chart marks ---------- */
document.addEventListener('mousemove', e => {
  const t = e.target.closest && e.target.closest('[data-tip]');
  if (!t) { tip.hidden = true; return; }
  tip.textContent = t.getAttribute('data-tip');
  tip.hidden = false;
  tip.style.left = `${Math.min(e.clientX + 12, window.innerWidth - 290)}px`;
  tip.style.top = `${e.clientY + 14}px`;
});

route();
