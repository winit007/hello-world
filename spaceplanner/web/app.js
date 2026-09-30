/* Space Planner web UI: wizard -> generate -> compare -> edit (drag walls, swap rooms, live re-score) -> export. */
(function () {
  'use strict';
  const $ = (s, el) => (el || document).querySelector(s);
  const $$ = (s, el) => Array.from((el || document).querySelectorAll(s));
  const STEPS = ['A', 'B', 'C', 'D', 'E', 'F', 'G'];
  const FILL = { living: '#fde9b5', dining: '#fdd9a8', kitchen: '#ffc4ad', master: '#d9ccf2', bedroom: '#e6dcf7', guest: '#e6dcf7', servant: '#e6dcf7', toilet: '#c4e4f2', utility: '#e0efe0', pooja: '#fff2b3', study: '#d3ecc4', balcony: '#dff0d3', store: '#e6e1d6', passage: '#f1f1f1', foyer: '#f1f1f1', dress: '#efe6fa', stair: '#e3e3e3', shop: '#fbe1c8', office: '#fbe9d0' };
  const state = { step: 0, defaults: null, session: null, result: null, option: null, floor: 0, selected: null, pending: false };

  // ---------- helpers ----------
  const ftin = ft => { const neg = ft < 0; ft = Math.abs(ft); let t = Math.round(ft * 12); let f = Math.floor(t / 12), i = t - f * 12; return `${neg ? '-' : ''}${f}'-${i}"`; };
  const m = ft => (ft * 0.3048).toFixed(2);
  const setDeep = (obj, path, val) => { const p = path.split('.'); let o = obj; for (let i = 0; i < p.length - 1; i++) { o[p[i]] = o[p[i]] || {}; o = o[p[i]]; } o[p[p.length - 1]] = val; };
  const num = v => (v === '' || v == null || isNaN(parseFloat(v))) ? null : parseFloat(v);

  // ---------- wizard ----------
  function showStep(i) {
    state.step = Math.max(0, Math.min(STEPS.length - 1, i));
    $$('.step').forEach((el, k) => el.classList.toggle('active', k === state.step));
    $$('#steps button').forEach((b, k) => b.classList.toggle('active', k === state.step));
    $('#prev').disabled = state.step === 0; $('#next').disabled = state.step === STEPS.length - 1;
  }
  function buildSteps() {
    const nav = $('#steps'); nav.innerHTML = '';
    STEPS.forEach((s, i) => { const b = document.createElement('button'); b.textContent = s + '. ' + $(`.step[data-step="${s}"] h2`).childNodes[0].textContent.replace(/^[A-G]\.\s*/, '').trim(); b.onclick = () => showStep(i); nav.appendChild(b); });
    $('#prev').onclick = () => showStep(state.step - 1); $('#next').onclick = () => showStep(state.step + 1);
    $('#site-mode').onchange = updateSiteMode; updateSiteMode();
  }
  function updateSiteMode() {
    const mode = $('#site-mode').value;
    $$('.rect-only').forEach(el => el.style.display = mode === 'rect' ? '' : 'none');
    $$('.area-only').forEach(el => el.style.display = mode === 'area' ? '' : 'none');
    $$('.poly-only').forEach(el => el.style.display = mode === 'poly' ? '' : 'none');
  }
  function buildDynamic(d) {
    const cfg = d.config;
    const mix = $('#unit-mix'); mix.innerHTML = '';
    ['1bhk', '2bhk', '3bhk', '4bhk', 'duplex'].forEach(t => { mix.insertAdjacentHTML('beforeend', `<label>${t.toUpperCase()} per floor<input name="mix.${t}" type="number" min="0" placeholder="0"></label>`); });
    const mins = $('#room-mins'); mins.innerHTML = '';
    Object.entries(cfg.rooms.defaults).forEach(([k, r]) => { if (['passage', 'foyer', 'shop', 'office', 'stair', 'dress'].includes(k)) return; mins.insertAdjacentHTML('beforeend', `<label>${r.label}<input name="min.${k}" value="${r.min[0]} x ${r.min[1]}"></label>`); });
    const sv = $('#services'); sv.innerHTML = '';
    ['ug_tank', 'oht', 'dg_set', 'transformer', 'meter_room', 'fire_pump', 'fire_tank', 'stp', 'septic_tank', 'rwh_pit', 'guard_room', 'garbage_room', 'society_office', 'driver_toilet', 'borewell'].forEach(s => {
      sv.insertAdjacentHTML('beforeend', `<label class="check"><input type="checkbox" name="svc.${s}"> ${s.replace(/_/g, ' ')}</label>`);
    });
    const vo = $('#vastu-overrides'); vo.innerHTML = '';
    Object.keys(cfg.vastu.rules).forEach(r => { vo.insertAdjacentHTML('beforeend', `<label>Vastu: ${r.replace(/_/g, ' ')}<select name="vo.${r}"><option value="">project default</option><option>strict</option><option>preferred</option><option>ignore</option></select></label>`); });
  }
  function readForm() {
    const f = $('#brief-form'); const brief = { project: {}, site: {}, regulatory: { setbacks: {} }, programme: {}, rooms: { options: {}, overrides: {} }, config: {}, commercial: {}, services_brief: {} };
    const data = {}; $$('input,select,textarea', f).forEach(el => { if (!el.name) return; data[el.name] = el.type === 'checkbox' ? el.checked : el.value; });
    const g = k => data[k];
    brief.project.type = g('project.type'); brief.project.goal = g('project.goal'); brief.project.vastu = g('project.vastu');
    const w = {}; ['carpet_efficiency', 'vastu', 'room_quality', 'light', 'circulation', 'structure'].forEach(k => { const v = num(g('w.' + k)); if (v != null) w[k] = v; });
    if (Object.keys(w).length) brief.project.goal_weights = w;
    const vo = {}; Object.keys(data).filter(k => k.startsWith('vo.') && data[k]).forEach(k => vo[k.slice(3)] = data[k]); if (Object.keys(vo).length) brief.project.vastu_overrides = vo;
    const mode = g('site.mode');
    if (mode === 'rect') { brief.site.length = g('site.length'); brief.site.breadth = g('site.breadth'); }
    else if (mode === 'area') { brief.site.kattha = num(g('site.kattha')) || 0; brief.site.dhur = num(g('site.dhur')) || 0; brief.site.area_sqft = num(g('site.area_sqft')) || 0; brief.site.aspect = num(g('site.aspect')) || 1.5; }
    else { brief.site.vertices = g('site.vertices').split('\n').map(l => l.trim()).filter(Boolean).map(l => l.split(',').map(s => s.trim())); }
    brief.site.north_deg = num(g('site.north_deg')) || 0; brief.site.corner = g('site.corner') === 'true';
    brief.site.roads = [{ side: g('site.road1.side'), width_m: num(g('site.road1.width_m')) || 9 }];
    if (g('site.road2.side')) brief.site.roads.push({ side: g('site.road2.side'), width_m: num(g('site.road2.width_m')) || 6 });
    if (g('site.entry_side')) brief.site.entry_side = g('site.entry_side'); if (g('site.vehicle_entry_side')) brief.site.vehicle_entry_side = g('site.vehicle_entry_side');
    brief.site.ground_vs_road = g('site.ground_vs_road') || 0;
    brief.site.adjoining = ['top', 'left', 'right', 'bottom'].map(s => ({ side: s, type: g('site.adj.' + s) }));
    brief.site.obstacles = (g('site.obstacles') || '').split('\n').map(l => l.trim()).filter(Boolean).map(l => { const p = l.split(','); return { name: p[0], x: p[1], y: p[2], radius: p[3] || 3 }; });
    brief.regulatory.authority = g('regulatory.authority');
    ['far', 'coverage_pct', 'max_floors'].forEach(k => { const v = num(g('regulatory.' + k)); if (v != null) brief.regulatory[k] = v; });
    ['front', 'rear', 'side1', 'side2'].forEach(k => { const v = g('regulatory.setbacks.' + k); if (v) brief.regulatory.setbacks[k] = v; });
    if (!Object.keys(brief.regulatory.setbacks).length) delete brief.regulatory.setbacks;
    brief.programme.floors = num(g('programme.floors')) || 1; brief.programme.stilt = g('programme.stilt') === 'true'; brief.programme.floor_to_floor = g('programme.floor_to_floor');
    const fpf = num(g('programme.flats_per_floor')); if (fpf) brief.programme.flats_per_floor = fpf;
    brief.programme.lifts_per_core = num(g('programme.lifts_per_core')) || 1; brief.programme.lift = g('programme.lift') === 'true';
    brief.programme.household_size = num(g('programme.household_size')); brief.programme.elderly = g('programme.elderly') === 'true';
    const mix = {}; ['1bhk', '2bhk', '3bhk', '4bhk', 'duplex'].forEach(t => { const v = num(g('mix.' + t)); if (v) mix[t] = v; }); if (Object.keys(mix).length) brief.programme.unit_mix = mix;
    ['shop_count', 'shop_frontage', 'shop_depth'].forEach(k => { const v = g('commercial.' + k); if (v) brief.commercial[k] = k === 'shop_count' ? num(v) : v; });
    ['pooja', 'store', 'guest', 'servant', 'dress', 'study'].forEach(k => { const v = g('rooms.options.' + k); if (v) brief.rooms.options[k] = v === 'true'; });
    Object.keys(data).filter(k => k.startsWith('min.')).forEach(k => { const p = data[k].split(/x/i).map(s => s.trim()); if (p.length === 2 && p[0] && p[1]) brief.rooms.overrides[k.slice(4)] = { min: p }; });
    const svc = Object.keys(data).filter(k => k.startsWith('svc.') && data[k]).map(k => k.slice(4)); if (svc.length) brief.services = svc;
    const dg = num(g('services_brief.dg_kva')); if (dg) brief.services_brief.dg_kva = dg;
    Object.keys(data).filter(k => k.startsWith('config.')).forEach(k => {
      let v = data[k]; if (v === '' || v == null) return;
      if (k.startsWith('config.construction.column_size_in.')) v = v.split(/x/i).map(s => parseFloat(s));
      else if (!isNaN(parseFloat(v)) && !/['"]/.test(v) && !k.endsWith('wall_material')) v = parseFloat(v);
      setDeep(brief, k, v);
    });
    Object.keys(data).filter(k => k.startsWith('win.')).forEach(k => { const p = data[k].split(/x/i).map(s => s.trim()); if (p.length === 2) { setDeep(brief, `config.windows.${k.slice(4)}.w`, p[0]); setDeep(brief, `config.windows.${k.slice(4)}.h`, p[1]); } });
    return { brief, n: num(g('n_options')) || 4 };
  }
  function fillForm(brief) {
    const f = $('#brief-form');
    const set = (name, v) => { const el = f.elements[name]; if (el && v != null) el.value = String(v); };
    set('project.type', brief.project.type); set('project.goal', brief.project.goal); set('project.vastu', brief.project.vastu);
    const s = brief.site;
    if (s.length) { $('#site-mode').value = 'rect'; set('site.length', s.length); set('site.breadth', s.breadth); }
    else if (s.kattha || s.area_sqft) { $('#site-mode').value = 'area'; set('site.kattha', s.kattha); set('site.dhur', s.dhur); set('site.area_sqft', s.area_sqft); set('site.aspect', s.aspect); }
    updateSiteMode();
    set('site.north_deg', s.north_deg); set('site.corner', s.corner ? 'true' : 'false');
    if (s.roads && s.roads[0]) { set('site.road1.side', s.roads[0].side); set('site.road1.width_m', s.roads[0].width_m); }
    set('site.road2.side', s.roads && s.roads[1] ? s.roads[1].side : ''); if (s.roads && s.roads[1]) set('site.road2.width_m', s.roads[1].width_m);
    set('site.entry_side', s.entry_side || ''); set('site.vehicle_entry_side', s.vehicle_entry_side || '');
    (s.adjoining || []).forEach(a => set('site.adj.' + a.side, a.type));
    set('regulatory.authority', (brief.regulatory || {}).authority);
    const p = brief.programme || {}; set('programme.floors', p.floors); set('programme.stilt', p.stilt ? 'true' : 'false'); set('programme.floor_to_floor', p.floor_to_floor); set('programme.flats_per_floor', p.flats_per_floor || ''); set('programme.lifts_per_core', p.lifts_per_core || 1);
    ['1bhk', '2bhk', '3bhk', '4bhk', 'duplex'].forEach(t => set('mix.' + t, (p.unit_mix || {})[t] || ''));
    const ro = (brief.rooms || {}).options || {}; ['pooja', 'store', 'guest', 'servant', 'dress', 'study'].forEach(k => set('rooms.options.' + k, ro[k] == null ? '' : String(ro[k])));
    $$('input[name^="svc."]').forEach(el => el.checked = (brief.services || []).includes(el.name.slice(4)));
  }

  // ---------- generate ----------
  async function generate() {
    const { brief, n } = readForm();
    const st = $('#status'); st.className = 'status'; st.textContent = 'Generating options… (a few seconds per option)';
    $('#generate').disabled = true;
    try {
      const r = await fetch('/api/generate', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ brief, n_options: n }) });
      const j = await r.json();
      if (!r.ok || j.error) throw new Error(j.error || r.statusText);
      state.session = j.session; state.result = j.result; st.textContent = '';
      showCompare();
    } catch (e) { st.className = 'status error'; st.textContent = 'Error: ' + e.message; }
    $('#generate').disabled = false;
  }
  function showCompare() {
    $('#wizard').classList.add('hidden'); $('#detail').classList.add('hidden'); $('#compare').classList.remove('hidden');
    const res = state.result;
    $('#global-warnings').innerHTML = res.warnings.map(w => '⚠ ' + esc(w)).join('<br>') || '';
    const cards = $('#option-cards'); cards.innerHTML = '';
    res.options.forEach(o => {
      const el = document.createElement('div'); el.className = 'opt';
      const fi = mainFloorIndex(o);
      el.innerHTML = `<div class="score">${o.score}<span class="hint"> / 100 · ${esc(o.name)}${o.feasible ? '' : ' · <span class="st-fail">incomplete</span>'}</span></div>
        <div class="summary">${esc(o.summary)}</div>
        <div class="bars">${Object.entries(o.scores).map(([k, v]) => `<div class="bar"><span style="width:110px">${k.replace(/_/g, ' ')}</span><i style="width:${Math.round(v * 80)}px"></i>${Math.round(v * 100)}%</div>`).join('')}</div>
        <div class="hint">FAR ${o.regulatory.far_used}/${o.regulatory.far_permitted} · coverage ${o.regulatory.coverage_pct}% · efficiency ${Math.round(o.areas.efficiency * 100)}% · parking ${o.areas.parking.ecs_provided}/${o.areas.parking.ecs_required} ECS</div>`;
      const svg = renderFloor(o, fi, { width: 420, grid: false, zones: false, openings: true, interactive: false });
      el.appendChild(svg);
      el.onclick = () => showDetail(o.id);
      cards.appendChild(el);
    });
    $('#back-to-wizard').onclick = () => { $('#compare').classList.add('hidden'); $('#wizard').classList.remove('hidden'); };
  }
  const mainFloorIndex = o => { const i = o.floors.findIndex(f => f.kind === 'typical' || f.kind === 'house'); return i < 0 ? 0 : i; };

  // ---------- detail ----------
  function showDetail(id) {
    state.option = state.result.options.find(o => o.id === id); state.floor = mainFloorIndex(state.option); state.selected = null;
    $('#compare').classList.add('hidden'); $('#detail').classList.remove('hidden');
    $('#detail-title').innerHTML = `${esc(state.option.name)} <span class="tag">${state.option.score} / 100</span> <button type="button" class="link" id="back-to-compare">all options</button>`;
    $('#back-to-compare').onclick = showCompare;
    const tabs = $('#floor-tabs'); tabs.innerHTML = '';
    state.option.floors.forEach((f, i) => { const b = document.createElement('button'); b.textContent = f.name + (f.repeat > 1 ? ` ×${f.repeat}` : ''); b.onclick = () => { state.floor = i; state.selected = null; renderDetail(); }; tabs.appendChild(b); });
    const sb = document.createElement('button'); sb.textContent = 'Site plan'; sb.onclick = () => { state.floor = -1; renderDetail(); }; tabs.appendChild(sb);
    ['show-grid', 'show-zones', 'show-openings'].forEach(id => $('#' + id).onchange = renderDetail);
    renderDetail();
  }
  function renderDetail() {
    const o = state.option; const plan = $('#plan'); plan.innerHTML = '';
    $$('#floor-tabs button').forEach((b, i) => b.classList.toggle('active', (state.floor === -1 ? i === o.floors.length : i === state.floor)));
    const base = `/api/export/${state.session}/${o.id}/`;
    $('#exports').innerHTML = [['dxf', 'DXF (AutoCAD)'], ['svg?floor=' + Math.max(state.floor, 0), 'SVG plan'], ['site.svg', 'SVG site'], ['md', 'Report (MD)'], ['html', 'Report (HTML)'], ['csv', 'Columns CSV'], ['json', 'JSON']]
      .map(([f, l]) => `<a href="${base}${f}" target="_blank">${l}</a>`).join('');
    if (state.floor === -1) { plan.appendChild(renderSite(o, 1000)); }
    else { plan.appendChild(renderFloor(o, state.floor, { width: 1000, grid: $('#show-grid').checked, zones: $('#show-zones').checked, openings: $('#show-openings').checked, interactive: true })); }
    renderSide();
  }
  function renderSide() {
    const o = state.option; const A = o.areas; const R = o.regulatory;
    $('#side-scores').innerHTML = Object.entries(o.scores).map(([k, v]) => `<div class="gauge"><div class="v">${Math.round(v * 100)}%</div><div class="l">${k.replace(/_/g, ' ')}</div></div>`).join('') +
      `<div class="hint">FAR ${R.far_used} / ${R.far_permitted} ${R.far_ok ? '✓' : '✗'} · coverage ${R.coverage_pct}% / ${R.coverage_pct_max}% ${R.coverage_ok ? '✓' : '✗'} · height ${R.height_label} ${R.height_ok ? '✓' : '✗'} · ${R.lift_required ? 'lift required' : 'no lift needed'}${R.high_rise ? ' · fire norms apply' : ''}</div>`;
    $('#side-areas').innerHTML = `<table><tr><th>Unit</th><th>Type</th><th>×</th><th>Carpet</th><th>Balcony</th><th>Built-up</th><th>Super</th></tr>` +
      A.units.map(u => `<tr><td>${esc(u.label)}</td><td>${u.type.toUpperCase()}</td><td>${u.count}</td><td>${u.carpet_sqft.toFixed(0)}</td><td>${u.balcony_sqft.toFixed(0)}</td><td>${u.builtup_sqft.toFixed(0)}</td><td>${u.super_builtup_sqft.toFixed(0)}</td></tr>`).join('') +
      `</table><div class="hint">Plot ${A.plot_label}<br>Footprint ${A.footprint_sqft.toFixed(0)} sq ft · total built-up ${A.total_builtup_sqft.toFixed(0)} sq ft · total carpet ${A.total_carpet_sqft.toFixed(0)} sq ft · efficiency ${Math.round(A.efficiency * 100)}% · parking ${A.parking.ecs_provided} / ${A.parking.ecs_required} ECS</div>`;
    const f = o.floors[Math.max(state.floor, 0)];
    $('#side-rooms').innerHTML = f.units.map(u => `<b>${esc(u.label)} (${u.type.toUpperCase()})</b>${u.error ? ` <span class="st-fail">${esc(u.error)}</span>` : ''}<table><tr><th>Room</th><th>Size</th><th>m</th><th>Zone</th></tr>` +
      u.rooms.map(r => `<tr><td>${esc(r.label)}${r.issues.length ? ' ⚠' : ''}</td><td>${r.size_label}</td><td>${m(r.w)}×${m(r.h)}</td><td>${r.zone}</td></tr>`).join('') + '</table>').join('');
    const vs = o.vastu.summary;
    $('#side-vastu').innerHTML = `<div>Score <b>${vs.score}%</b> · ${vs.pass} pass · ${vs.partial} partial · ${vs.fail} fail</div><table><tr><th>Subject</th><th>Zone</th><th>Expected</th><th>Status</th></tr>` +
      o.vastu.rows.map(r => `<tr><td>${esc(r.subject)}</td><td>${r.zone}</td><td>${r.expected}</td><td class="st-${r.status}">${r.status}</td></tr>`).join('') + '</table>';
    const sc = o.schedule;
    $('#side-schedule').innerHTML = `<table><tr><th>Tag</th><th>Size</th><th>Count</th></tr>` + [...Object.values(sc.doors), ...Object.values(sc.windows)].map(e => `<tr><td>${e.tag}</td><td>${ftin(e.w)} × ${ftin(e.h)}${e.sliding ? ' sliding' : ''}${e.kind === 'ventilator' ? ' vent.' : ''}</td><td>${e.count}</td></tr>`).join('') + '</table>';
    $('#side-warnings').innerHTML = '<ul>' + o.warnings.map(w => `<li>${esc(w)}</li>`).join('') + '</ul>';
  }

  // ---------- client renderer ----------
  const NS = 'http://www.w3.org/2000/svg';
  function mk(tag, attrs, parent) { const el = document.createElementNS(NS, tag); Object.entries(attrs || {}).forEach(([k, v]) => el.setAttribute(k, v)); if (parent) parent.appendChild(el); return el; }
  function renderFloor(o, fi, opt) {
    const f = o.floors[fi]; const pl = f.plate; const pad = 8;
    const minx = pl.x - pad, miny = pl.y - pad, maxx = pl.x + pl.w + pad, maxy = pl.y + pl.h + pad + 3;
    const W = opt.width, s = (W - 40) / (maxx - minx), H = (maxy - miny) * s + 40;
    const svg = mk('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, 'font-family': 'Segoe UI, Arial, sans-serif' });
    const P = (x, y) => [20 + (x - minx) * s, 20 + (maxy - y) * s];
    const rect = (r, a, parent) => { const [x, y] = P(r.x, r.y + r.h); return mk('rect', Object.assign({ x, y, width: r.w * s, height: r.h * s }, a), parent || svg); };
    const line = (x1, y1, x2, y2, a, parent) => { const [ax, ay] = P(x1, y1), [bx, by] = P(x2, y2); return mk('line', Object.assign({ x1: ax, y1: ay, x2: bx, y2: by }, a), parent || svg); };
    const text = (x, y, str, a, parent) => { const [px, py] = P(x, y); const t = mk('text', Object.assign({ x: px, y: py, 'text-anchor': 'middle', 'dominant-baseline': 'middle', 'font-size': 10 }, a), parent || svg); t.textContent = str; return t; };
    mk('rect', { width: W, height: H, fill: '#fff' }, svg);
    rect(pl, { fill: '#fff', stroke: '#111', 'stroke-width': 3 });
    if (opt.zones) { for (let i = 1; i < 3; i++) { line(pl.x + i * pl.w / 3, pl.y, pl.x + i * pl.w / 3, pl.y + pl.h, { stroke: '#bbb', 'stroke-dasharray': '4,4' }); line(pl.x, pl.y + i * pl.h / 3, pl.x + pl.w, pl.y + i * pl.h / 3, { stroke: '#bbb', 'stroke-dasharray': '4,4' }); } }
    if (f.corridor) { rect(f.corridor, { fill: '#f3f3f3', stroke: '#333' }); text(f.corridor.x + f.corridor.w / 2, f.corridor.y + f.corridor.h / 2, 'CORRIDOR', { 'font-size': 9, fill: '#666' }); }
    Object.entries(f.core || {}).forEach(([n, r]) => { if (n === 'block') rect(r, { fill: '#dcdcdc', stroke: '#333' }); });
    Object.entries(f.core || {}).forEach(([n, r]) => { if (n !== 'block') { rect(r, { fill: '#eee', stroke: '#333' }); text(r.x + r.w / 2, r.y + r.h / 2, n.toUpperCase(), { 'font-size': 8, fill: '#444' }); } });
    (f.extra_rooms || []).forEach(e => { rect(e.rect, { fill: FILL[e.type] || '#f4f4f4', stroke: '#333' }); text(e.rect.x + e.rect.w / 2, e.rect.y + e.rect.h / 2 + 0.8, e.label, { 'font-weight': 'bold', 'font-size': 9 }); text(e.rect.x + e.rect.w / 2, e.rect.y + e.rect.h / 2 - 0.9, `${ftin(e.rect.w)} x ${ftin(e.rect.h)}`, { 'font-size': 7.5, fill: '#555' }); });
    (f.parking || []).forEach(b => rect(b, { fill: '#f6f6f6', stroke: '#888', 'stroke-width': 0.6 }));
    f.units.forEach(u => {
      rect(u.rect, { fill: 'none', stroke: '#111', 'stroke-width': 2.2 });
      u.rooms.forEach(r => {
        const cl = r.clear; const el = rect(cl, { fill: FILL[r.type] || '#f4f4f4', stroke: '#222', 'stroke-width': 1.4, class: 'room' + (state.selected && state.selected.unit === u.label && state.selected.key === r.key ? ' sel' : '') });
        el.dataset.unit = u.label; el.dataset.key = r.key;
        if (opt.interactive) { el.style.cursor = 'pointer'; el.addEventListener('click', () => onRoomClick(u, r)); }
        const big = Math.min(cl.w, cl.h) * s > 26;
        const cx = cl.x + cl.w / 2, cy = cl.y + cl.h / 2;
        if (big) { text(cx, cy + 1.1, r.label, { 'font-weight': 'bold', 'font-size': 9, 'pointer-events': 'none' }); text(cx, cy - 0.2, `${ftin(cl.w)} x ${ftin(cl.h)}`, { 'font-size': 7.5, fill: '#444', 'pointer-events': 'none' }); text(cx, cy - 1.4, `${(cl.w * cl.h).toFixed(0)} sq ft · ${(cl.w * cl.h * 0.0929).toFixed(1)} m²`, { 'font-size': 6.5, fill: '#666', 'pointer-events': 'none' }); if (opt.zones) text(cl.x + 1, cl.y + cl.h - 0.9, r.zone, { 'font-size': 7, fill: '#7a4', 'text-anchor': 'start', 'pointer-events': 'none' }); }
        else if (Math.min(cl.w, cl.h) * s > 12) text(cx, cy, r.label.slice(0, 14), { 'font-weight': 'bold', 'font-size': 6.5, 'pointer-events': 'none' });
        if (r.issues && r.issues.length) { const [px, py] = P(cl.x + cl.w - 0.8, cl.y + cl.h - 0.8); const c = mk('circle', { cx: px, cy: py, r: 3.5, fill: '#d97706' }, svg); const t = mk('title', {}, c); t.textContent = r.issues.join('; '); }
      });
      if (opt.openings) u.rooms.forEach(r => {
        if (r.door) drawDoor(r.door);
        (r.windows || []).forEach(w => { const [x1, y1, x2, y2] = w.seg; line(x1, y1, x2, y2, { stroke: '#fff', 'stroke-width': 3.2 }); const h = Math.abs(y2 - y1) < 1e-6; if (h) { line(x1, y1 - 0.2, x2, y1 - 0.2, { stroke: '#2a7ab8' }); line(x1, y1 + 0.2, x2, y1 + 0.2, { stroke: '#2a7ab8' }); } else { line(x1 - 0.2, y1, x1 - 0.2, y2, { stroke: '#2a7ab8' }); line(x1 + 0.2, y1, x1 + 0.2, y2, { stroke: '#2a7ab8' }); } });
      });
      text(u.rect.x + u.rect.w / 2, u.rect.y + u.rect.h + 1.2, `${u.label} · ${u.type.toUpperCase()} · carpet ${u.carpet_sqft.toFixed(0)} sq ft`, { 'font-size': 8, fill: '#333' });
      if (opt.interactive && u.tree) addWallHandles(u, svg, P, s);
    });
    function drawDoor(d) {
      const [x1, y1, x2, y2] = d.seg;
      if (d.opening) { line(x1, y1, x2, y2, { stroke: '#fff', 'stroke-width': 3 }); line(x1, y1, x2, y2, { stroke: '#999', 'stroke-width': 0.8, 'stroke-dasharray': '3,3' }); return; }
      line(x1, y1, x2, y2, { stroke: '#fff', 'stroke-width': 3.2 });
      const w = d.w, horiz = Math.abs(y2 - y1) < 1e-6;
      if (d.sliding) { if (horiz) { line(x1, y1 - 0.25, x1 + w / 2, y1 - 0.25, { stroke: '#444', 'stroke-width': 1.2 }); line(x1 + w / 2, y1 + 0.25, x2, y1 + 0.25, { stroke: '#444', 'stroke-width': 1.2 }); } else { line(x1 - 0.25, y1, x1 - 0.25, y1 + w / 2, { stroke: '#444', 'stroke-width': 1.2 }); line(x1 + 0.25, y1 + w / 2, x1 + 0.25, y2, { stroke: '#444', 'stroke-width': 1.2 }); } return; }
      let ex, ey, a0, a1;
      if (horiz) { const into = d.side === 'top' ? -1 : 1; ex = x1; ey = y1 + into * w; a0 = into > 0 ? 0 : -90; a1 = into > 0 ? 90 : 0; }
      else { const into = d.side === 'right' ? -1 : 1; ex = x1 + into * w; ey = y1; a0 = into > 0 ? 0 : 90; a1 = into > 0 ? 90 : 180; }
      line(x1, y1, ex, ey, { stroke: '#444', 'stroke-width': 1.2 });
      const [sx, sy] = P(x1 + w * Math.cos(a0 * Math.PI / 180), y1 + w * Math.sin(a0 * Math.PI / 180)); const [tx, ty] = P(x1 + w * Math.cos(a1 * Math.PI / 180), y1 + w * Math.sin(a1 * Math.PI / 180));
      mk('path', { d: `M ${sx} ${sy} A ${w * s} ${w * s} 0 0 ${a1 > a0 ? 0 : 1} ${tx} ${ty}`, fill: 'none', stroke: '#444', 'stroke-width': 0.8 }, svg);
    }
    (f.shafts || []).forEach(sh => { const r = sh.rect; rect(r, { fill: '#fff', stroke: '#2a7ab8', 'stroke-width': 0.8 }); line(r.x, r.y, r.x + r.w, r.y + r.h, { stroke: '#2a7ab8', 'stroke-width': 0.6 }); line(r.x, r.y + r.h, r.x + r.w, r.y, { stroke: '#2a7ab8', 'stroke-width': 0.6 }); });
    const cols = f.columns;
    if (opt.grid && cols) {
      cols.grid_x.forEach((x, i) => { line(x, pl.y - 3, x, pl.y + pl.h + 3, { stroke: '#888', 'stroke-width': 0.5, 'stroke-dasharray': '6,3' }); const [px, py] = P(x, pl.y + pl.h + 4.2); mk('circle', { cx: px, cy: py, r: 7, fill: '#fff', stroke: '#555', 'stroke-width': 0.8 }, svg); text(x, pl.y + pl.h + 4.2, cols.labels_x[i], { 'font-size': 8 }); });
      cols.grid_y.forEach((y, i) => { line(pl.x - 3, y, pl.x + pl.w + 3, y, { stroke: '#888', 'stroke-width': 0.5, 'stroke-dasharray': '6,3' }); const [px, py] = P(pl.x - 4.2, y); mk('circle', { cx: px, cy: py, r: 7, fill: '#fff', stroke: '#555', 'stroke-width': 0.8 }, svg); text(pl.x - 4.2, y, cols.labels_y[i], { 'font-size': 8 }); });
      cols.columns.forEach(c => rect({ x: c.x - c.w / 2, y: c.y - c.d / 2, w: c.w, h: c.d }, { fill: '#222' }));
    }
    const y0 = pl.y - 5.5; line(pl.x, y0, pl.x + pl.w, y0, { stroke: '#333', 'stroke-width': 0.8 }); text(pl.x + pl.w / 2, y0 - 1.3, `${ftin(pl.w)} (${m(pl.w)} m)`, { 'font-size': 8 });
    const x0 = pl.x + pl.w + 5.5; line(x0, pl.y, x0, pl.y + pl.h, { stroke: '#333', 'stroke-width': 0.8 }); const [tx, ty] = P(x0 + 1.3, pl.y + pl.h / 2); const tt = mk('text', { x: tx, y: ty, 'font-size': 8, 'text-anchor': 'middle', transform: `rotate(-90 ${tx} ${ty})` }, svg); tt.textContent = `${ftin(pl.h)} (${m(pl.h)} m)`;
    const fs = o.front_side; const lab = { bottom: [pl.x + pl.w / 2, pl.y - 7.3], top: [pl.x + pl.w / 2, pl.y + pl.h + 7.3], left: [pl.x - 7.3, pl.y + pl.h / 2], right: [pl.x + pl.w + 7.3, pl.y + pl.h / 2] }[fs];
    text(lab[0], lab[1], 'ROAD / ENTRY SIDE', { 'font-size': 8, fill: '#a33', 'font-weight': 'bold' });
    northArrow(svg, P, maxx - 6, maxy - 5, o.plot.north_deg, s);
    text(minx + 1, maxy - 2, `${o.name} · ${f.name}` + (f.repeat > 1 ? ` (×${f.repeat})` : ''), { 'font-size': 11, 'font-weight': 'bold', 'text-anchor': 'start' });
    return svg;
  }
  function northArrow(svg, P, x, y, deg, s) {
    const a = deg * Math.PI / 180, dx = Math.sin(a), dy = Math.cos(a), L = 2.5;
    const [ax, ay] = P(x - dx * L, y - dy * L), [bx, by] = P(x + dx * L, y + dy * L);
    mk('line', { x1: ax, y1: ay, x2: bx, y2: by, stroke: '#c0392b', 'stroke-width': 1.5 }, svg); mk('circle', { cx: bx, cy: by, r: 3, fill: '#c0392b' }, svg);
    const [tx, ty] = P(x + dx * (L + 1.5), y + dy * (L + 1.5)); const t = mk('text', { x: tx, y: ty, 'font-size': 12, fill: '#c0392b', 'font-weight': 'bold', 'text-anchor': 'middle', 'dominant-baseline': 'middle' }, svg); t.textContent = 'N';
  }
  function renderSite(o, width) {
    const plot = o.plot, poly = plot.polygon; const xs = poly.map(p => p[0]), ys = poly.map(p => p[1]); const pad = 10;
    const minx = Math.min(...xs) - pad, miny = Math.min(...ys) - pad, maxx = Math.max(...xs) + pad, maxy = Math.max(...ys) + pad + 4;
    const W = width, s = (W - 40) / (maxx - minx), H = (maxy - miny) * s + 40;
    const svg = mk('svg', { viewBox: `0 0 ${W} ${H}`, width: W, height: H, 'font-family': 'Segoe UI, Arial, sans-serif' });
    const P = (x, y) => [20 + (x - minx) * s, 20 + (maxy - y) * s];
    const pts = arr => arr.map(p => P(p[0], p[1]).join(',')).join(' ');
    const rect = (r, a) => { const [x, y] = P(r.x, r.y + r.h); return mk('rect', Object.assign({ x, y, width: r.w * s, height: r.h * s }, a), svg); };
    const text = (x, y, str, a) => { const [px, py] = P(x, y); const t = mk('text', Object.assign({ x: px, y: py, 'text-anchor': 'middle', 'dominant-baseline': 'middle', 'font-size': 9 }, a), svg); t.textContent = str; return t; };
    mk('rect', { width: W, height: H, fill: '#fff' }, svg);
    mk('polygon', { points: pts(poly), fill: '#f9f7ee', stroke: '#111', 'stroke-width': 2 }, svg);
    mk('polygon', { points: pts(plot.envelope), fill: 'none', stroke: '#c33', 'stroke-dasharray': '6,4' }, svg);
    const fp = o.footprint; rect(fp, { fill: '#d9d9d9', stroke: '#111', 'stroke-width': 1.8 }); text(fp.x + fp.w / 2, fp.y + fp.h / 2, `BUILDING ${ftin(fp.w)} x ${ftin(fp.h)}`, { 'font-weight': 'bold', 'font-size': 10 });
    if (o.site.driveway.length) mk('polygon', { points: pts(o.site.driveway), fill: '#e8e8e8', stroke: '#777' }, svg);
    o.site.parking.forEach(b => { rect(b, { fill: '#fff', stroke: '#666', 'stroke-width': 0.7 }); text(b.x + b.w / 2, b.y + b.h / 2, 'P', { fill: '#666', 'font-size': 8 }); });
    o.site.services.forEach(sv => { if (!sv.placed) return; const r = sv.rect; const ug = ['ug_tank', 'septic_tank', 'rwh_pit', 'fire_tank', 'stp', 'borewell'].includes(sv.name); rect(r, { fill: ug ? '#cfe8ff' : sv.name === 'oht' ? '#ffe0b3' : '#e8dcc8', stroke: '#444', 'stroke-dasharray': ug ? '3,2' : '' }); text(r.x + r.w / 2, r.y + r.h / 2, sv.label, { 'font-size': 7 }); });
    const g = o.site.gate; const [gx, gy] = P(g.x, g.y); mk('rect', { x: gx - 6, y: gy - 6, width: 12, height: 12, fill: '#c33' }, svg); text(g.x, g.y - 3.5, 'GATE', { fill: '#c33', 'font-weight': 'bold', 'font-size': 8 });
    plot.edges.forEach(e => { const mx = (e.a[0] + e.b[0]) / 2, my = (e.a[1] + e.b[1]) / 2, dx = e.b[0] - e.a[0], dy = e.b[1] - e.a[1], L = Math.hypot(dx, dy) || 1, nx = dy / L, ny = -dx / L; let ang = -Math.atan2(dy, dx) * 180 / Math.PI; if (ang > 90 || ang < -90) ang += 180; const lab = (e.road_width_m ? `ROAD ${e.road_width_m} m · ` : `${e.adjoining} · `) + `${e.compass} · ${e.role} · setback ${ftin(e.setback_ft)}`; const [px, py] = P(mx + nx * 4.5, my + ny * 4.5); const t = mk('text', { x: px, y: py, 'font-size': 8, 'text-anchor': 'middle', transform: `rotate(${ang} ${px} ${py})` }, svg); t.textContent = lab; const [qx, qy] = P(mx - nx * 2.5, my - ny * 2.5); const t2 = mk('text', { x: qx, y: qy, 'font-size': 7.5, fill: '#555', 'text-anchor': 'middle', transform: `rotate(${ang} ${qx} ${qy})` }, svg); t2.textContent = `${ftin(e.length_ft)} (${m(e.length_ft)} m)`; });
    northArrow(svg, P, maxx - 6, maxy - 1, plot.north_deg, s);
    text(minx + 1, maxy - 1.5, `SITE PLAN · plot ${plot.area_label}`, { 'font-size': 11, 'font-weight': 'bold', 'text-anchor': 'start' });
    return svg;
  }

  // ---------- editing: drag walls & swap rooms ----------
  function nodeRects(node, rooms) {
    // returns rect of node from its leaves' cells
    const leaves = []; (function walk(n) { if (n.kind === 'leaf') leaves.push(n); else n.children.forEach(walk); })(node);
    const cells = leaves.map(l => rooms[l.key]).filter(Boolean).map(r => r.cell); if (!cells.length) return null;
    const x = Math.min(...cells.map(c => c.x)), y = Math.min(...cells.map(c => c.y)), x2 = Math.max(...cells.map(c => c.x + c.w)), y2 = Math.max(...cells.map(c => c.y + c.h));
    return { x, y, w: x2 - x, h: y2 - y };
  }
  function addWallHandles(u, svg, P, s) {
    const rooms = {}; u.rooms.forEach(r => rooms[r.key] = r);
    (function walk(node) {
      if (node.kind === 'leaf') return;
      const kids = node.children.map(c => nodeRects(c, rooms));
      for (let i = 0; i < node.children.length - 1; i++) {
        const a = kids[i], b = kids[i + 1]; if (!a || !b) continue;
        let el;
        if (node.kind === 'V') { const x = a.x + a.w; const y1 = Math.max(a.y, b.y), y2 = Math.min(a.y + a.h, b.y + b.h); const [px, py1] = P(x, y2); const [, py2] = P(x, y1); el = mk('rect', { x: px - 4, y: py1, width: 8, height: py2 - py1, fill: 'rgba(37,99,235,0.0)', class: 'wall-handle' }, svg); }
        else { const y = a.y + a.h; const x1 = Math.max(a.x, b.x), x2 = Math.min(a.x + a.w, b.x + b.w); const [px1, py] = P(x1, y); const [px2] = P(x2, y); el = mk('rect', { x: px1, y: py - 4, width: px2 - px1, height: 8, fill: 'rgba(37,99,235,0.0)', class: 'wall-handle h' }, svg); }
        el.addEventListener('mouseenter', () => el.setAttribute('fill', 'rgba(37,99,235,0.35)')); el.addEventListener('mouseleave', () => el.setAttribute('fill', 'rgba(37,99,235,0)'));
        el.addEventListener('pointerdown', ev => startDrag(ev, u, node, i, kids, svg, s));
      }
      node.children.forEach(walk);
    })(u.tree);
  }
  function startDrag(ev, u, node, i, kids, svg, s) {
    ev.preventDefault(); ev.stopPropagation();
    const start = node.kind === 'V' ? ev.clientX : ev.clientY;
    const shares = kids.map(k => node.kind === 'V' ? k.w : k.h);
    const rect = svg.getBoundingClientRect(); const scale = (svg.viewBox.baseVal.width / rect.width) / s;   // px -> ft
    const orig = shares.slice();
    const move = e => {
      let d = ((node.kind === 'V' ? e.clientX : e.clientY) - start) * scale; if (node.kind === 'H') d = -d;
      d = Math.max(-orig[i] + 3, Math.min(orig[i + 1] - 3, d));
      shares[i] = orig[i] + d; shares[i + 1] = orig[i + 1] - d;
      $('#edit-status').textContent = `wall at ${ftin(shares[i])} from the room edge…`;
    };
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); const before = JSON.stringify(u.tree); node.ratio = shares.map(v => Math.round(v * 100) / 100); rescore(u, before); };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', up);
  }
  function onRoomClick(u, r) {
    if (state.selected && state.selected.unit === u.label && state.selected.key !== r.key) {
      // swap the two leaves in the tree
      const a = state.selected.key, b = r.key;
      const before = JSON.stringify(u.tree);
      (function walk(n) { if (n.kind === 'leaf') { if (n.key === a) n.key = b; else if (n.key === b) n.key = a; } else n.children.forEach(walk); })(u.tree);
      state.selected = null; rescore(u, before);
    } else if (state.selected && state.selected.unit === u.label && state.selected.key === r.key) { state.selected = null; renderDetail(); }
    else { state.selected = { unit: u.label, key: r.key }; renderDetail(); $('#edit-status').textContent = `${r.label} selected · click another room in ${u.label} to swap`; }
  }
  async function rescore(u, before) {
    if (state.pending) return; state.pending = true; $('#edit-status').textContent = 'Re-scoring…';
    try {
      const r = await fetch('/api/rescore', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ session: state.session, option: state.option.id, floor: state.floor, unit: u.label, tree: u.tree }) });
      const j = await r.json();
      if (!r.ok || j.error) { if (before) u.tree = JSON.parse(before); throw new Error((j.error || r.statusText) + ' Edit reverted.'); }
      const f = state.option.floors[state.floor]; const idx = f.units.findIndex(x => x.label === u.label);
      f.units[idx] = j.unit; if (j.unit.tree) f.units[idx].tree = j.unit.tree;
      const viol = j.violations || []; $('#edit-status').innerHTML = viol.length ? `<span class="st-fail">${viol.map(esc).join(' · ')}</span>` : `<span class="st-pass">OK · unit score ${j.unit.score}</span>`;
      // refresh totals shown in the side panel (server keeps option totals in sync)
      const us = state.option.areas.units.find(x => x.label === u.label && x.floor === f.name); if (us) { us.carpet_sqft = j.unit.carpet_sqft; us.score = j.unit.score; us.vastu = (j.unit.scores || {}).vastu; }
      renderDetail();
    } catch (e) { $('#edit-status').innerHTML = `<span class="st-fail">${esc(e.message)}</span>`; }
    state.pending = false;
  }
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ---------- samples ----------
  const SAMPLE_HOUSE = { project: { type: 'house', goal: 'vastu', vastu: 'preferred' }, site: { length: 40, breadth: 60, north_deg: 90, roads: [{ side: 'bottom', width_m: 9 }], corner: false, adjoining: [{ side: 'top', type: 'building' }, { side: 'left', type: 'open plot' }, { side: 'right', type: 'building' }] }, regulatory: { authority: 'Patna Nagar Nigam' }, programme: { floors: 2, floor_to_floor: "10'0\"" }, rooms: { options: { pooja: true, store: true } }, services: ['ug_tank', 'oht', 'septic_tank', 'rwh_pit'] };
  const SAMPLE_APT = { project: { type: 'apartment', goal: 'balanced', vastu: 'preferred' }, site: { kattha: 10, aspect: 1.4, north_deg: 0, corner: true, roads: [{ side: 'bottom', width_m: 12 }, { side: 'right', width_m: 9 }], entry_side: 'bottom', vehicle_entry_side: 'right' }, regulatory: { authority: 'Patna Nagar Nigam' }, programme: { floors: 5, stilt: true, floor_to_floor: "10'0\"", unit_mix: { '2bhk': 2, '3bhk': 2 }, flats_per_floor: 4, lifts_per_core: 1 }, services: ['ug_tank', 'oht', 'dg_set', 'meter_room', 'guard_room', 'garbage_room', 'septic_tank', 'rwh_pit', 'society_office'] };

  // ---------- boot ----------
  async function init() {
    buildSteps();
    const r = await fetch('/api/defaults'); state.defaults = await r.json(); buildDynamic(state.defaults);
    showStep(0);
    $('#generate').onclick = generate;
    $('#load-house').onclick = () => { fillForm(SAMPLE_HOUSE); showStep(0); };
    $('#load-apt').onclick = () => { fillForm(SAMPLE_APT); showStep(0); };
  }
  init();
})();
