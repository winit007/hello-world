/* Apartment Plan Maker – floor plan editor with Vaastu, ventilation and layout checks. */
(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // Configuration
  // ---------------------------------------------------------------------------
  const ROOM_TYPES = {
    living:   { label: 'Living Room',     color: '#f6d365', habitable: true,  minArea: 120, minWidth: 10, size: [14, 12], vaastu: { ideal: ['N', 'E', 'NE'], ok: ['NW', 'W', 'C'], bad: ['SW'] } },
    dining:   { label: 'Dining',          color: '#fdb87d', habitable: true,  minArea: 80,  minWidth: 8,  size: [10, 8],  vaastu: { ideal: ['W', 'E'], ok: ['N', 'S', 'NW', 'SE', 'C'], bad: ['SW', 'NE'] } },
    master:   { label: 'Master Bedroom',  color: '#c3b1e1', habitable: true,  minArea: 120, minWidth: 10, size: [12, 12], vaastu: { ideal: ['SW'], ok: ['S', 'W', 'NW'], bad: ['NE', 'SE', 'C'] } },
    bedroom:  { label: 'Bedroom',         color: '#dccff2', habitable: true,  minArea: 100, minWidth: 8,  size: [11, 10], vaastu: { ideal: ['W', 'NW', 'S'], ok: ['SW', 'E'], bad: ['NE', 'SE', 'C'] } },
    kitchen:  { label: 'Kitchen',         color: '#ff9e80', habitable: true,  minArea: 50,  minWidth: 6,  size: [8, 10],  vaastu: { ideal: ['SE'], ok: ['NW'], bad: ['NE', 'SW', 'C', 'N'] } },
    pooja:    { label: 'Pooja Room',      color: '#ffe9a8', habitable: false, minArea: 9,   minWidth: 3,  size: [4, 4],   vaastu: { ideal: ['NE'], ok: ['E', 'N', 'C'], bad: ['S', 'SW', 'SE', 'NW', 'W'] } },
    toilet:   { label: 'Toilet / Bath',   color: '#a8dcef', habitable: false, minArea: 18,  minWidth: 4,  size: [5, 7],   vaastu: { ideal: ['NW', 'W'], ok: ['S', 'SE'], bad: ['NE', 'C', 'SW', 'E', 'N'] }, needsVent: true },
    study:    { label: 'Study',           color: '#b5e7a0', habitable: true,  minArea: 60,  minWidth: 7,  size: [8, 8],   vaastu: { ideal: ['NE', 'E', 'W'], ok: ['N', 'NW', 'SW'], bad: ['SE', 'C'] } },
    balcony:  { label: 'Balcony',         color: '#cdeac0', habitable: false, minArea: 20,  minWidth: 3,  size: [8, 4],   vaastu: { ideal: ['N', 'E', 'NE'], ok: ['W', 'NW'], bad: ['SW', 'S', 'SE'] } },
    store:    { label: 'Store',           color: '#d9d2c5', habitable: false, minArea: 15,  minWidth: 3,  size: [4, 4],   vaastu: { ideal: ['SW', 'W', 'S'], ok: ['NW', 'SE'], bad: ['NE', 'C'] } },
    entrance: { label: 'Main Entrance',   color: '#9ad0c2', habitable: false, minArea: 0,   minWidth: 2,  size: [4, 2],   vaastu: { ideal: ['N', 'E', 'NE'], ok: ['W', 'NW'], bad: ['SW', 'SE', 'S'] } },
    foyer:    { label: 'Foyer / Passage', color: '#ececec', habitable: false, minArea: 0,   minWidth: 3,  size: [6, 6],   vaastu: null },
  };
  const MAJOR_TYPES = ['kitchen', 'master', 'pooja', 'toilet', 'entrance'];
  const HEAVY_TYPES = ['toilet', 'kitchen', 'store'];
  const ZONE_NAMES = { N: 'North', NE: 'North-East', E: 'East', SE: 'South-East', S: 'South', SW: 'South-West', W: 'West', NW: 'North-West', C: 'Centre (Brahmasthan)' };
  const ZONE_TINT = { NE: 'rgba(96,165,250,0.16)', SE: 'rgba(251,146,60,0.16)', SW: 'rgba(161,98,7,0.14)', NW: 'rgba(148,163,184,0.18)', C: 'rgba(250,204,21,0.14)', N: 'rgba(96,165,250,0.07)', E: 'rgba(96,165,250,0.07)', S: 'rgba(251,146,60,0.07)', W: 'rgba(148,163,184,0.09)' };
  const NORTH_ANGLE = { top: 0, right: 90, bottom: 180, left: 270 };
  const SIDES = ['top', 'right', 'bottom', 'left'];
  const SIDE_VEC = { top: [0, -1], right: [1, 0], bottom: [0, 1], left: [-1, 0] };
  const STORAGE_KEY = 'apartment-planner-v1';
  const EPS = 0.01;
  const SNAP = 0.5;

  // ---------------------------------------------------------------------------
  // State
  // ---------------------------------------------------------------------------
  const state = {
    plot: { w: 32, h: 36, north: 'top', open: { top: false, right: true, bottom: true, left: true }, ventRatio: 10, windowHeight: 4 },
    rooms: [],
    selectedId: null,
    showZones: true,
    showGrid: true,
    filter: 'all',
    hidePass: false,
  };
  let nextId = 1;
  let result = null;

  function sampleRooms() {
    const mk = (type, name, x, y, w, h, windows, extra) => Object.assign({ id: nextId++, type, name, x, y, w, h, windows: Object.assign({ top: 0, right: 0, bottom: 0, left: 0 }, windows || {}), exhaust: false }, extra || {});
    return [
      mk('entrance', 'Main Entrance', 10, 0, 4, 2),
      mk('pooja', 'Pooja', 14, 0, 4, 4),
      mk('living', 'Living Room', 18, 0, 14, 14, { right: 8 }),
      mk('bedroom', 'Bedroom 2', 0, 0, 10, 14, { left: 5 }),
      mk('toilet', 'Common Toilet', 0, 14, 5, 8, { left: 2 }),
      mk('toilet', 'Master Toilet', 5, 14, 5, 8, {}, { exhaust: true }),
      mk('foyer', 'Foyer / Passage', 10, 4, 8, 18),
      mk('dining', 'Dining', 18, 14, 14, 8, { right: 5 }),
      mk('master', 'Master Bedroom', 0, 22, 14, 14, { left: 6, bottom: 6 }),
      mk('bedroom', 'Kids Bedroom', 14, 22, 8, 14, { bottom: 5 }),
      mk('kitchen', 'Kitchen', 22, 22, 10, 14, { right: 4, bottom: 3 }),
    ];
  }

  function loadSample() {
    nextId = 1;
    state.plot = { w: 32, h: 36, north: 'top', open: { top: false, right: true, bottom: true, left: true }, ventRatio: 10, windowHeight: 4 };
    state.rooms = sampleRooms();
    state.selectedId = null;
  }

  // ---------------------------------------------------------------------------
  // Geometry helpers
  // ---------------------------------------------------------------------------
  const near = (a, b) => Math.abs(a - b) < EPS;
  const snap = v => Math.round(v / SNAP) * SNAP;
  const fmt = v => (Math.round(v * 10) / 10).toString();
  const area = r => r.w * r.h;

  // Compass direction of a screen-space vector (x right, y down) given where north lies.
  function dirFromVec(dx, dy, north) {
    if (dx === 0 && dy === 0) return 'C';
    const screenAngle = Math.atan2(dx, -dy) * 180 / Math.PI; // 0 = up, clockwise
    const bearing = ((screenAngle - NORTH_ANGLE[north]) % 360 + 360) % 360;
    const dirs = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'];
    return dirs[Math.round(bearing / 45) % 8];
  }

  // Vaastu zone (3 x 3 grid) of a point on the plot.
  function zoneOfPoint(px, py, plot) {
    const col = px < plot.w / 3 ? -1 : px < 2 * plot.w / 3 ? 0 : 1;
    const row = py < plot.h / 3 ? -1 : py < 2 * plot.h / 3 ? 0 : 1;
    return dirFromVec(col, row, plot.north);
  }
  const zoneOfRoom = (r, plot) => zoneOfPoint(r.x + r.w / 2, r.y + r.h / 2, plot);

  function overlapArea(a, b) {
    const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
    const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
    return w > EPS && h > EPS ? w * h : 0;
  }

  function shareWall(a, b) {
    const overlapX = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
    const overlapY = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
    const touchV = (near(a.x + a.w, b.x) || near(b.x + b.w, a.x)) && overlapY > 0.5;
    const touchH = (near(a.y + a.h, b.y) || near(b.y + b.h, a.y)) && overlapX > 0.5;
    return touchV || touchH;
  }

  function wallsInfo(r, plot) {
    return SIDES.map(side => {
      const onBoundary = side === 'top' ? near(r.y, 0) : side === 'right' ? near(r.x + r.w, plot.w) : side === 'bottom' ? near(r.y + r.h, plot.h) : near(r.x, 0);
      const exterior = onBoundary && !!plot.open[side];
      const length = side === 'top' || side === 'bottom' ? r.w : r.h;
      return { side, dir: dirFromVec(SIDE_VEC[side][0], SIDE_VEC[side][1], plot.north), onBoundary, exterior, length, window: r.windows[side] || 0 };
    });
  }

  function roomSizeLabel(r) { return `${fmt(r.w)} × ${fmt(r.h)} ft`; }

  function findFreeSpot(w, h) {
    const plot = state.plot;
    for (let y = 0; y + h <= plot.h + EPS; y += 1) {
      for (let x = 0; x + w <= plot.w + EPS; x += 1) {
        const probe = { x, y, w, h };
        if (!state.rooms.some(r => overlapArea(r, probe) > 0)) return [x, y];
      }
    }
    return [0, 0];
  }

  // ---------------------------------------------------------------------------
  // Rule engine
  // ---------------------------------------------------------------------------
  function evaluate(s) {
    const checks = [];
    const plot = s.plot;
    const rooms = s.rooms;
    const add = (cat, weight, status, msg, roomId) => checks.push({ cat, weight, status, msg, roomId: roomId == null ? null : roomId });

    // ----- Layout
    rooms.forEach(r => {
      const t = ROOM_TYPES[r.type];
      if (r.x < -EPS || r.y < -EPS || r.x + r.w > plot.w + EPS || r.y + r.h > plot.h + EPS) {
        add('layout', 3, 'fail', `${r.name} extends outside the plot.`, r.id);
      }
      if (t.minArea > 0) {
        if (area(r) < t.minArea - EPS) add('layout', 2, 'fail', `${r.name} is ${fmt(area(r))} sq ft; a ${t.label.toLowerCase()} needs at least ${t.minArea} sq ft.`, r.id);
        else add('layout', 1, 'pass', `${r.name} is ${fmt(area(r))} sq ft, above the ${t.minArea} sq ft minimum.`, r.id);
      }
      if (Math.min(r.w, r.h) < t.minWidth - EPS) add('layout', 1, 'warn', `${r.name} is only ${fmt(Math.min(r.w, r.h))} ft wide; ${t.minWidth} ft is the comfortable minimum.`, r.id);
    });
    for (let i = 0; i < rooms.length; i++) {
      for (let j = i + 1; j < rooms.length; j++) {
        if (overlapArea(rooms[i], rooms[j]) > 0) add('layout', 3, 'fail', `${rooms[i].name} overlaps ${rooms[j].name}.`, rooms[i].id);
      }
    }
    const has = type => rooms.some(r => r.type === type);
    [['living', 'a living room'], ['kitchen', 'a kitchen'], ['toilet', 'a toilet'], ['entrance', 'a main entrance']].forEach(([type, label]) => {
      if (has(type)) add('layout', 2, 'pass', `The plan has ${label}.`);
      else add('layout', 2, 'fail', `The plan has no ${label.replace(/^an? /, '')}. Add one from the palette.`);
    });
    if (has('master') || has('bedroom')) add('layout', 2, 'pass', 'The plan has at least one bedroom.');
    else add('layout', 2, 'fail', 'The plan has no bedroom.');

    if (rooms.length) {
      const used = rooms.reduce((sum, r) => sum + area(r), 0);
      const passages = rooms.filter(r => r.type === 'foyer').reduce((sum, r) => sum + area(r), 0);
      const circulation = Math.max(0, plot.w * plot.h - used) + passages;
      const pct = circulation / (plot.w * plot.h) * 100;
      if (pct < 5) add('layout', 1, 'warn', `Only ${fmt(pct)}% of the plot is left for passages and circulation; aim for 5% or more.`);
      else if (pct > 35) add('layout', 1, 'warn', `${fmt(pct)}% of the plot is unallocated or passage. Consider using that space.`);
      else add('layout', 1, 'pass', `${fmt(pct)}% of the plot is circulation space.`);
    }

    // ----- Ventilation
    const openCount = SIDES.filter(sd => plot.open[sd]).length;
    if (openCount === 0) add('ventilation', 3, 'fail', 'No side of the flat is open to outside air, so nothing can be ventilated. Tick the open sides under Plot.');
    else if (openCount === 1) add('ventilation', 1, 'warn', 'Only one side of the flat is open to outside air, so cross ventilation across the flat is not possible.');
    else add('ventilation', 1, 'pass', `${openCount} sides of the flat are open to outside air.`);

    rooms.forEach(r => {
      const t = ROOM_TYPES[r.type];
      if (!t.habitable && !t.needsVent) return;
      const walls = wallsInfo(r, plot);
      const extWindows = walls.filter(w => w.window > 0 && w.exterior);
      const blocked = walls.filter(w => w.window > 0 && !w.exterior);
      const extSides = walls.filter(w => w.exterior);

      if (t.needsVent) {
        if (extWindows.length) add('ventilation', 2, 'pass', `${r.name} has a window to the outside.`, r.id);
        else if (r.exhaust) add('ventilation', 2, 'warn', `${r.name} has no window and relies on a mechanical exhaust fan.`, r.id);
        else if (extSides.length) add('ventilation', 2, 'fail', `${r.name} has no window or exhaust. Add a window on its ${extSides.map(w => w.dir).join(' or ')} wall.`, r.id);
        else add('ventilation', 2, 'fail', `${r.name} is an internal toilet with no window. Tick "mechanical exhaust" or move it to an outer wall.`, r.id);
        return;
      }

      if (extWindows.length === 0) {
        if (blocked.length) add('ventilation', 3, 'fail', `${r.name}: its window on the ${blocked.map(w => w.dir).join('/')} wall faces a shared wall, so it brings in no fresh air.`, r.id);
        else if (!extSides.length) add('ventilation', 3, 'fail', `${r.name} touches no exterior wall, so it cannot have a window. Move it to an outer wall.`, r.id);
        else add('ventilation', 3, 'fail', `${r.name} has no window. Add one on its ${extSides.map(w => w.dir).join(' or ')} wall.`, r.id);
        return;
      }
      add('ventilation', 3, 'pass', `${r.name} has a window to the outside on the ${extWindows.map(w => w.dir).join(' and ')}.`, r.id);
      const winArea = extWindows.reduce((sum, w) => sum + w.window, 0) * plot.windowHeight;
      const need = area(r) * plot.ventRatio / 100;
      if (winArea >= need - EPS) add('ventilation', 2, 'pass', `${r.name} window area ${fmt(winArea)} sq ft meets the ${fmt(need)} sq ft requirement (${plot.ventRatio}% of floor).`, r.id);
      else if (winArea >= need / 2) add('ventilation', 2, 'warn', `${r.name} window area ${fmt(winArea)} sq ft is below the ${fmt(need)} sq ft requirement. Widen the window.`, r.id);
      else add('ventilation', 2, 'fail', `${r.name} window area ${fmt(winArea)} sq ft is less than half of the ${fmt(need)} sq ft requirement.`, r.id);
      if (new Set(extWindows.map(w => w.side)).size >= 2) add('ventilation', 1, 'pass', `${r.name} has windows on two walls, giving cross ventilation.`, r.id);
      else if (extSides.length >= 2) add('ventilation', 1, 'warn', `${r.name} is ventilated from one side only. Add a window on its ${extSides.filter(w => w.window === 0).map(w => w.dir).join(' or ')} wall for cross ventilation.`, r.id);
      else add('ventilation', 1, 'warn', `${r.name} is ventilated from one side only; cross ventilation is not possible where it stands.`, r.id);
    });

    // ----- Vaastu
    rooms.forEach(r => {
      const t = ROOM_TYPES[r.type];
      if (!t.vaastu) return;
      const z = zoneOfRoom(r, plot);
      const w = MAJOR_TYPES.includes(r.type) ? 3 : 2;
      const pref = t.vaastu.ideal.map(d => ZONE_NAMES[d]).join(' or ');
      if (t.vaastu.ideal.includes(z)) add('vaastu', w, 'pass', `${r.name} sits in the ${ZONE_NAMES[z]} zone, the ideal placement.`, r.id);
      else if (t.vaastu.ok.includes(z)) add('vaastu', w, 'pass', `${r.name} in the ${ZONE_NAMES[z]} zone is acceptable (ideal: ${pref}).`, r.id);
      else if (t.vaastu.bad.includes(z)) add('vaastu', w, 'fail', `${r.name} should not be in the ${ZONE_NAMES[z]} zone. Move it towards the ${pref}.`, r.id);
      else add('vaastu', w, 'warn', `${r.name} in the ${ZONE_NAMES[z]} zone is neutral; ${pref} is preferred.`, r.id);
    });

    if (rooms.length) {
      const cell = { x: plot.w / 3, y: plot.h / 3, w: plot.w / 3, h: plot.h / 3 };
      const heavy = rooms.filter(r => HEAVY_TYPES.includes(r.type)).reduce((sum, r) => sum + overlapArea(r, cell), 0);
      const pct = heavy / (cell.w * cell.h) * 100;
      if (pct > 25) add('vaastu', 3, 'fail', `${fmt(pct)}% of the Brahmasthan (centre) is taken by toilets, kitchen or store. The centre should stay open.`);
      else if (pct > 5) add('vaastu', 3, 'warn', `${fmt(pct)}% of the Brahmasthan (centre) is taken by toilets, kitchen or store. Keep it clear if you can.`);
      else add('vaastu', 3, 'pass', 'The Brahmasthan (centre) is free of toilets, kitchen and store.');
    }

    const poojas = rooms.filter(r => r.type === 'pooja');
    const toilets = rooms.filter(r => r.type === 'toilet');
    const kitchens = rooms.filter(r => r.type === 'kitchen');
    poojas.forEach(p => {
      const adj = toilets.find(tl => shareWall(p, tl));
      if (adj) add('vaastu', 2, 'fail', `${p.name} shares a wall with ${adj.name}. A pooja room should not touch a toilet.`, p.id);
      else add('vaastu', 2, 'pass', `${p.name} does not touch any toilet.`, p.id);
    });
    kitchens.forEach(k => {
      const adj = toilets.find(tl => shareWall(k, tl));
      if (adj) add('vaastu', 1, 'warn', `${k.name} shares a wall with ${adj.name}. Kitchens and toilets are best kept apart.`, k.id);
      else add('vaastu', 1, 'pass', `${k.name} is kept apart from toilets.`, k.id);
    });
    if (rooms.some(r => r.windows.top || r.windows.right || r.windows.bottom || r.windows.left)) {
      const ne = rooms.some(r => wallsInfo(r, plot).some(w => w.window > 0 && w.exterior && ['N', 'E', 'NE'].includes(w.dir)));
      if (ne) add('vaastu', 1, 'pass', 'There are openings facing North or East, letting in morning light.');
      else add('vaastu', 1, 'warn', 'No window faces North or East. Vaastu prefers more openings on those sides.');
    }
    const masters = rooms.filter(r => r.type === 'master');
    const others = rooms.filter(r => r.type === 'bedroom');
    if (masters.length && others.length) {
      const maxMaster = Math.max(...masters.map(area));
      const maxOther = Math.max(...others.map(area));
      if (maxMaster >= maxOther) add('vaastu', 1, 'pass', 'The master bedroom is the largest bedroom.');
      else add('vaastu', 1, 'warn', 'Another bedroom is larger than the master bedroom. Vaastu prefers the master to be the largest.');
    }
    rooms.filter(r => r.type === 'entrance').forEach(e => {
      const walls = wallsInfo(e, plot);
      if (!walls.some(w => w.onBoundary)) add('vaastu', 1, 'warn', `${e.name} does not touch the outer wall of the flat. Place it on the boundary where the door is.`, e.id);
    });

    const credit = { pass: 1, warn: 0.5, fail: 0 };
    const score = list => {
      const total = list.reduce((sum, c) => sum + c.weight, 0);
      if (!total) return null;
      return Math.round(list.reduce((sum, c) => sum + c.weight * credit[c.status], 0) / total * 100);
    };
    const counts = list => ({ pass: list.filter(c => c.status === 'pass').length, warn: list.filter(c => c.status === 'warn').length, fail: list.filter(c => c.status === 'fail').length });
    const cats = ['vaastu', 'ventilation', 'layout'];
    const scores = { overall: { score: score(checks), counts: counts(checks) } };
    cats.forEach(cat => { const list = checks.filter(c => c.cat === cat); scores[cat] = { score: score(list), counts: counts(list) }; });
    const byRoom = new Map();
    checks.forEach(c => {
      if (c.roomId == null || c.status === 'pass') return;
      const e = byRoom.get(c.roomId) || { warn: 0, fail: 0 };
      e[c.status]++;
      byRoom.set(c.roomId, e);
    });
    return { checks, scores, byRoom };
  }

  // ---------------------------------------------------------------------------
  // Canvas rendering
  // ---------------------------------------------------------------------------
  const canvas = document.getElementById('plan');
  const ctx = canvas.getContext('2d');
  const PAD = 44;
  let view = { ox: PAD, oy: PAD, sc: 10, cw: 0, ch: 0 };

  function resizeCanvas() {
    const wrap = canvas.parentElement;
    const cw = Math.max(320, wrap.clientWidth - 20);
    const plot = state.plot;
    const scW = (cw - 2 * PAD) / plot.w;
    let ch = Math.round(plot.h * scW + 2 * PAD);
    const maxH = Math.max(420, window.innerHeight - 160);
    if (ch > maxH) ch = maxH;
    const sc = Math.min(scW, (ch - 2 * PAD) / plot.h);
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(cw * dpr);
    canvas.height = Math.round(ch * dpr);
    canvas.style.width = cw + 'px';
    canvas.style.height = ch + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    view = { ox: (cw - plot.w * sc) / 2, oy: (ch - plot.h * sc) / 2, sc, cw, ch };
  }

  const toPx = (x, y) => [view.ox + x * view.sc, view.oy + y * view.sc];
  const toFt = (px, py) => [(px - view.ox) / view.sc, (py - view.oy) / view.sc];

  function draw() {
    const plot = state.plot;
    const { ox, oy, sc, cw, ch } = view;
    ctx.clearRect(0, 0, cw, ch);
    ctx.fillStyle = '#fafafa';
    ctx.fillRect(0, 0, cw, ch);

    // plot background
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(ox, oy, plot.w * sc, plot.h * sc);

    // zones
    if (state.showZones) {
      for (let c = -1; c <= 1; c++) {
        for (let r = -1; r <= 1; r++) {
          const z = dirFromVec(c, r, plot.north);
          const x = ox + (c + 1) * plot.w / 3 * sc;
          const y = oy + (r + 1) * plot.h / 3 * sc;
          ctx.fillStyle = ZONE_TINT[z];
          ctx.fillRect(x, y, plot.w / 3 * sc, plot.h / 3 * sc);
          ctx.fillStyle = 'rgba(30,41,59,0.35)';
          ctx.font = `bold ${Math.max(11, Math.min(18, sc * 1.4))}px sans-serif`;
          ctx.textAlign = 'center';
          ctx.textBaseline = 'middle';
          ctx.fillText(z === 'C' ? 'CENTRE' : z, x + plot.w / 6 * sc, y + plot.h / 6 * sc);
        }
      }
      ctx.strokeStyle = 'rgba(30,41,59,0.25)';
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 1;
      for (let i = 1; i < 3; i++) {
        ctx.beginPath(); ctx.moveTo(ox + plot.w / 3 * i * sc, oy); ctx.lineTo(ox + plot.w / 3 * i * sc, oy + plot.h * sc); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(ox, oy + plot.h / 3 * i * sc); ctx.lineTo(ox + plot.w * sc, oy + plot.h / 3 * i * sc); ctx.stroke();
      }
      ctx.setLineDash([]);
    }

    // grid
    if (state.showGrid && sc >= 5) {
      ctx.strokeStyle = 'rgba(0,0,0,0.05)';
      ctx.lineWidth = 1;
      for (let x = 0; x <= plot.w; x += 1) { ctx.beginPath(); ctx.moveTo(ox + x * sc, oy); ctx.lineTo(ox + x * sc, oy + plot.h * sc); ctx.stroke(); }
      for (let y = 0; y <= plot.h; y += 1) { ctx.beginPath(); ctx.moveTo(ox, oy + y * sc); ctx.lineTo(ox + plot.w * sc, oy + y * sc); ctx.stroke(); }
    }

    // rooms
    const sel = state.rooms.find(r => r.id === state.selectedId);
    state.rooms.forEach(r => drawRoom(r, r === sel));

    // plot boundary, side by side
    const corners = { top: [[0, 0], [plot.w, 0]], right: [[plot.w, 0], [plot.w, plot.h]], bottom: [[plot.w, plot.h], [0, plot.h]], left: [[0, plot.h], [0, 0]] };
    SIDES.forEach(side => {
      const [[x1, y1], [x2, y2]] = corners[side];
      const open = plot.open[side];
      ctx.strokeStyle = open ? '#2563eb' : '#374151';
      ctx.lineWidth = open ? 4 : 6;
      ctx.beginPath(); ctx.moveTo(...toPx(x1, y1)); ctx.lineTo(...toPx(x2, y2)); ctx.stroke();
      // side label
      const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
      const [px, py] = toPx(mx, my);
      const offs = { top: [0, -14], bottom: [0, 14], left: [-14, 0], right: [14, 0] }[side];
      ctx.fillStyle = open ? '#2563eb' : '#6b7280';
      ctx.font = '11px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      const label = `${dirFromVec(SIDE_VEC[side][0], SIDE_VEC[side][1], plot.north)} · ${open ? 'open' : 'shared'}`;
      if (side === 'left' || side === 'right') {
        ctx.save(); ctx.translate(px + offs[0], py + offs[1]); ctx.rotate(side === 'left' ? -Math.PI / 2 : Math.PI / 2); ctx.fillText(label, 0, 0); ctx.restore();
      } else {
        ctx.fillText(label, px + offs[0], py + offs[1]);
      }
    });

    // dimensions
    ctx.fillStyle = '#6b7280';
    ctx.font = '11px sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'bottom';
    ctx.fillText(`${fmt(plot.w)} ft × ${fmt(plot.h)} ft · ${fmt(plot.w * plot.h)} sq ft`, 8, ch - 6);

    drawCompass();
  }

  function drawRoom(r, selected) {
    const { sc } = view;
    const t = ROOM_TYPES[r.type];
    const [px, py] = toPx(r.x, r.y);
    const pw = r.w * sc, ph = r.h * sc;
    ctx.fillStyle = t.color;
    ctx.globalAlpha = 0.85;
    ctx.fillRect(px, py, pw, ph);
    ctx.globalAlpha = 1;
    ctx.strokeStyle = selected ? '#2563eb' : '#374151';
    ctx.lineWidth = selected ? 3 : 2;
    ctx.strokeRect(px, py, pw, ph);

    // windows
    const walls = wallsInfo(r, state.plot);
    walls.forEach(w => {
      if (!w.window) return;
      const len = Math.min(w.window, w.length) * sc;
      const thick = 7;
      ctx.fillStyle = w.exterior ? '#3b82f6' : '#ef4444';
      let wx, wy, ww, wh;
      if (w.side === 'top') { wx = px + pw / 2 - len / 2; wy = py - thick / 2; ww = len; wh = thick; }
      else if (w.side === 'bottom') { wx = px + pw / 2 - len / 2; wy = py + ph - thick / 2; ww = len; wh = thick; }
      else if (w.side === 'left') { wx = px - thick / 2; wy = py + ph / 2 - len / 2; ww = thick; wh = len; }
      else { wx = px + pw - thick / 2; wy = py + ph / 2 - len / 2; ww = thick; wh = len; }
      ctx.fillRect(wx, wy, ww, wh);
      ctx.fillStyle = '#ffffff';
      if (ww > wh) ctx.fillRect(wx, wy + thick / 2 - 1, ww, 2); else ctx.fillRect(wx + thick / 2 - 1, wy, 2, wh);
    });

    // labels
    ctx.fillStyle = '#1f2937';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const big = Math.min(pw, ph) > 40 && pw > 70;
    const fs = Math.max(9, Math.min(13, sc * 1.1));
    ctx.font = `bold ${fs}px sans-serif`;
    const cx = px + pw / 2, cy = py + ph / 2;
    if (big) {
      ctx.fillText(r.name, cx, cy - fs * 0.7);
      ctx.font = `${fs - 1}px sans-serif`;
      ctx.fillStyle = '#4b5563';
      ctx.fillText(`${roomSizeLabel(r)} · ${fmt(area(r))} sq ft`, cx, cy + fs * 0.7);
    } else if (pw > 24 && ph > 14) {
      ctx.font = `bold ${Math.max(8, fs - 2)}px sans-serif`;
      ctx.fillText(r.name.length > 12 ? r.name.slice(0, 11) + '…' : r.name, cx, cy);
    }

    // issue marker
    const issues = result && result.byRoom.get(r.id);
    if (issues) {
      const color = issues.fail ? '#dc2626' : '#d97706';
      ctx.fillStyle = color;
      ctx.beginPath(); ctx.arc(px + pw - 9, py + 9, 7, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 9px sans-serif';
      ctx.fillText(String(issues.fail + issues.warn), px + pw - 9, py + 9.5);
    }

    // resize handle
    if (selected) {
      ctx.fillStyle = '#2563eb';
      ctx.fillRect(px + pw - 6, py + ph - 6, 12, 12);
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.strokeRect(px + pw - 6, py + ph - 6, 12, 12);
    }
  }

  function drawCompass() {
    const r = 16;
    const cx = view.cw - r - 10, cy = r + 10;
    ctx.save();
    ctx.translate(cx, cy);
    ctx.fillStyle = '#fff'; ctx.strokeStyle = '#374151'; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(0, 0, r, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
    ctx.rotate(NORTH_ANGLE[state.plot.north] * Math.PI / 180);
    ctx.fillStyle = '#dc2626';
    ctx.beginPath(); ctx.moveTo(0, -r + 3); ctx.lineTo(4, 3); ctx.lineTo(-4, 3); ctx.closePath(); ctx.fill();
    ctx.fillStyle = '#374151';
    ctx.beginPath(); ctx.moveTo(0, r - 3); ctx.lineTo(4, -3); ctx.lineTo(-4, -3); ctx.closePath(); ctx.fill();
    ctx.fillStyle = '#dc2626'; ctx.font = 'bold 9px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    ctx.fillText('N', 0, -r - 7);
    ctx.restore();
  }

  // ---------------------------------------------------------------------------
  // Pointer interaction
  // ---------------------------------------------------------------------------
  let drag = null;

  function roomAt(fx, fy) {
    for (let i = state.rooms.length - 1; i >= 0; i--) {
      const r = state.rooms[i];
      if (fx >= r.x && fx <= r.x + r.w && fy >= r.y && fy <= r.y + r.h) return r;
    }
    return null;
  }

  function wallNear(r, px, py, tol) {
    const [x, y] = toPx(r.x, r.y);
    const w = r.w * view.sc, h = r.h * view.sc;
    const inX = px >= x - tol && px <= x + w + tol;
    const inY = py >= y - tol && py <= y + h + tol;
    if (inX && Math.abs(py - y) <= tol) return 'top';
    if (inX && Math.abs(py - (y + h)) <= tol) return 'bottom';
    if (inY && Math.abs(px - x) <= tol) return 'left';
    if (inY && Math.abs(px - (x + w)) <= tol) return 'right';
    return null;
  }

  function pointerPos(e) {
    const rect = canvas.getBoundingClientRect();
    return [e.clientX - rect.left, e.clientY - rect.top];
  }

  canvas.addEventListener('pointerdown', e => {
    const [px, py] = pointerPos(e);
    const [fx, fy] = toFt(px, py);
    const sel = state.rooms.find(r => r.id === state.selectedId);
    if (sel) {
      const [hx, hy] = toPx(sel.x + sel.w, sel.y + sel.h);
      if (Math.abs(px - hx) <= 10 && Math.abs(py - hy) <= 10) {
        drag = { mode: 'resize', room: sel, moved: false };
        canvas.setPointerCapture(e.pointerId);
        return;
      }
    }
    const r = roomAt(fx, fy);
    const wasSelected = r && r.id === state.selectedId;
    if (r) {
      // bring to front and select
      state.rooms.splice(state.rooms.indexOf(r), 1);
      state.rooms.push(r);
      state.selectedId = r.id;
      drag = { mode: 'move', room: r, dx: fx - r.x, dy: fy - r.y, moved: false, wasSelected, downPx: [px, py] };
      canvas.setPointerCapture(e.pointerId);
    } else {
      state.selectedId = null;
      drag = null;
    }
    refresh(true);
  });

  canvas.addEventListener('pointermove', e => {
    const [px, py] = pointerPos(e);
    const [fx, fy] = toFt(px, py);
    if (!drag) {
      const sel = state.rooms.find(r => r.id === state.selectedId);
      let cursor = 'default';
      if (sel) {
        const [hx, hy] = toPx(sel.x + sel.w, sel.y + sel.h);
        if (Math.abs(px - hx) <= 10 && Math.abs(py - hy) <= 10) cursor = 'nwse-resize';
        else if (wallNear(sel, px, py, 6)) cursor = 'crosshair';
      }
      if (cursor === 'default' && roomAt(fx, fy)) cursor = 'move';
      canvas.style.cursor = cursor;
      return;
    }
    const r = drag.room;
    const plot = state.plot;
    if (drag.mode === 'move') {
      const nx = Math.min(Math.max(0, snap(fx - drag.dx)), plot.w - r.w);
      const ny = Math.min(Math.max(0, snap(fy - drag.dy)), plot.h - r.h);
      if (nx !== r.x || ny !== r.y) { r.x = nx; r.y = ny; drag.moved = true; refresh(false); }
      else if (Math.hypot(px - drag.downPx[0], py - drag.downPx[1]) > 4) drag.moved = true;
    } else {
      const nw = Math.min(Math.max(2, snap(fx - r.x)), plot.w - r.x);
      const nh = Math.min(Math.max(2, snap(fy - r.y)), plot.h - r.y);
      if (nw !== r.w || nh !== r.h) { r.w = nw; r.h = nh; drag.moved = true; refresh(false); }
    }
  });

  function endDrag(e) {
    if (!drag) return;
    const d = drag;
    drag = null;
    if (d.mode === 'move' && !d.moved && d.wasSelected) {
      const [px, py] = pointerPos(e);
      const side = wallNear(d.room, px, py, 8);
      if (side) toggleWindow(d.room, side);
    }
    refresh(true);
    persist();
  }
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);

  function toggleWindow(r, side) {
    const len = side === 'top' || side === 'bottom' ? r.w : r.h;
    if (r.windows[side] > 0) r.windows[side] = 0;
    else r.windows[side] = Math.max(1, Math.min(r.type === 'toilet' ? 2 : 4, len - 1));
  }

  document.addEventListener('keydown', e => {
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) return;
    const r = state.rooms.find(x => x.id === state.selectedId);
    if (!r) return;
    const step = e.shiftKey ? 1 : SNAP;
    if (e.key === 'Delete' || e.key === 'Backspace') { deleteRoom(r.id); e.preventDefault(); return; }
    if (e.key === 'ArrowLeft') r.x = Math.max(0, r.x - step);
    else if (e.key === 'ArrowRight') r.x = Math.min(state.plot.w - r.w, r.x + step);
    else if (e.key === 'ArrowUp') r.y = Math.max(0, r.y - step);
    else if (e.key === 'ArrowDown') r.y = Math.min(state.plot.h - r.h, r.y + step);
    else return;
    e.preventDefault();
    refresh(true);
    persist();
  });

  // ---------------------------------------------------------------------------
  // Room management
  // ---------------------------------------------------------------------------
  function addRoom(type) {
    const t = ROOM_TYPES[type];
    const w = Math.min(t.size[0], state.plot.w), h = Math.min(t.size[1], state.plot.h);
    const [x, y] = findFreeSpot(w, h);
    const count = state.rooms.filter(r => r.type === type).length;
    const room = { id: nextId++, type, name: count ? `${t.label} ${count + 1}` : t.label, x, y, w, h, windows: { top: 0, right: 0, bottom: 0, left: 0 }, exhaust: false };
    state.rooms.push(room);
    state.selectedId = room.id;
    refresh(true);
    persist();
  }

  function deleteRoom(id) {
    state.rooms = state.rooms.filter(r => r.id !== id);
    if (state.selectedId === id) state.selectedId = null;
    refresh(true);
    persist();
  }

  // ---------------------------------------------------------------------------
  // Side panels
  // ---------------------------------------------------------------------------
  const el = id => document.getElementById(id);

  function buildPalette() {
    const pal = el('room-palette');
    pal.innerHTML = '';
    Object.entries(ROOM_TYPES).forEach(([type, t]) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.innerHTML = `<span class="swatch" style="background:${t.color}"></span>${t.label}`;
      b.addEventListener('click', () => addRoom(type));
      pal.appendChild(b);
    });
    const rules = el('rules-vaastu');
    rules.innerHTML = '';
    Object.values(ROOM_TYPES).filter(t => t.vaastu).forEach(t => {
      const li = document.createElement('li');
      li.textContent = `${t.label}: ideal ${t.vaastu.ideal.join('/')}, acceptable ${t.vaastu.ok.join('/') || 'none'}, avoid ${t.vaastu.bad.join('/')}.`;
      rules.appendChild(li);
    });
    ['Brahmasthan (centre) kept free of toilet, kitchen and store.', 'Pooja room must not touch a toilet; kitchen kept apart from toilets.', 'Openings preferred on the North and East.', 'Master bedroom should be the largest bedroom.'].forEach(txt => {
      const li = document.createElement('li'); li.textContent = txt; rules.appendChild(li);
    });
    el('legend').innerHTML = '<span><i style="background:#2563eb"></i>Open side</span><span><i style="background:#374151"></i>Shared wall</span><span><i style="background:#3b82f6"></i>Window (ventilating)</span><span><i style="background:#ef4444"></i>Window on shared wall</span>';
  }

  function syncPlotInputs() {
    const p = state.plot;
    el('plot-w').value = p.w; el('plot-h').value = p.h; el('plot-north').value = p.north;
    SIDES.forEach(s => { el('open-' + s).checked = !!p.open[s]; });
    el('vent-ratio').value = p.ventRatio; el('win-h').value = p.windowHeight;
    el('show-zones').checked = state.showZones; el('show-grid').checked = state.showGrid;
  }

  function bindPlotInputs() {
    const num = (id, key, min, max) => el(id).addEventListener('change', () => {
      let v = parseFloat(el(id).value);
      if (isNaN(v)) v = state.plot[key];
      v = Math.min(max, Math.max(min, v));
      state.plot[key] = v;
      el(id).value = v;
      state.rooms.forEach(r => { r.w = Math.min(r.w, state.plot.w); r.h = Math.min(r.h, state.plot.h); r.x = Math.min(r.x, state.plot.w - r.w); r.y = Math.min(r.y, state.plot.h - r.h); });
      resizeCanvas(); refresh(true); persist();
    });
    num('plot-w', 'w', 8, 200); num('plot-h', 'h', 8, 200); num('vent-ratio', 'ventRatio', 1, 50); num('win-h', 'windowHeight', 1, 10);
    el('plot-north').addEventListener('change', () => { state.plot.north = el('plot-north').value; refresh(true); persist(); });
    SIDES.forEach(s => el('open-' + s).addEventListener('change', () => { state.plot.open[s] = el('open-' + s).checked; refresh(true); persist(); }));
    el('show-zones').addEventListener('change', () => { state.showZones = el('show-zones').checked; draw(); persist(); });
    el('show-grid').addEventListener('change', () => { state.showGrid = el('show-grid').checked; draw(); persist(); });
  }

  function renderProps() {
    const body = el('props-body');
    const r = state.rooms.find(x => x.id === state.selectedId);
    if (!r) { body.innerHTML = '<p class="hint">Select a room on the plan to edit it.</p>'; return; }
    const t = ROOM_TYPES[r.type];
    const zone = t.vaastu ? zoneOfRoom(r, state.plot) : null;
    const walls = wallsInfo(r, state.plot);
    const sideLabel = { top: 'Top', right: 'Right', bottom: 'Bottom', left: 'Left' };
    body.innerHTML = `
      <div class="props">
        <p><strong>${escapeHtml(r.name)}</strong>${zone ? `<span class="zone-badge">${ZONE_NAMES[zone]}</span>` : ''}</p>
        <label>Name<input type="text" id="p-name" value="${escapeHtml(r.name)}"></label>
        <label>Type<select id="p-type">${Object.entries(ROOM_TYPES).map(([k, v]) => `<option value="${k}" ${k === r.type ? 'selected' : ''}>${v.label}</option>`).join('')}</select></label>
        <div class="field-row">
          <label>X (ft)<input type="number" id="p-x" step="0.5" min="0" value="${r.x}"></label>
          <label>Y (ft)<input type="number" id="p-y" step="0.5" min="0" value="${r.y}"></label>
          <label>Width (ft)<input type="number" id="p-w" step="0.5" min="2" value="${r.w}"></label>
          <label>Depth (ft)<input type="number" id="p-h" step="0.5" min="2" value="${r.h}"></label>
        </div>
        <p class="hint">Area ${fmt(area(r))} sq ft${t.minArea ? ` (minimum ${t.minArea})` : ''}.</p>
        <p style="margin-bottom:4px"><strong>Windows</strong> <span class="hint">width in ft, 0 for none</span></p>
        <div class="walls">
          ${walls.map(w => `
            <div class="wall ${w.exterior ? 'ext' : (w.window > 0 ? 'blocked' : '')}">
              <label class="check"><input type="checkbox" data-wall="${w.side}" ${w.window > 0 ? 'checked' : ''}> ${sideLabel[w.side]} wall (${w.dir})</label>
              <input type="number" data-wall-width="${w.side}" min="0" max="${w.length}" step="0.5" value="${w.window}" ${w.window > 0 ? '' : 'disabled'}>
              <small>${w.exterior ? 'Open to outside air' : w.onBoundary ? 'On a shared side of the flat' : 'Internal wall'}</small>
            </div>`).join('')}
        </div>
        ${t.needsVent ? `<label class="check"><input type="checkbox" id="p-exhaust" ${r.exhaust ? 'checked' : ''}> Has mechanical exhaust fan</label>` : ''}
        <div class="actions">
          <button type="button" id="p-duplicate">Duplicate</button>
          <button type="button" class="danger" id="p-delete">Delete room</button>
        </div>
      </div>`;

    const commit = () => { refresh(true); persist(); };
    el('p-name').addEventListener('change', () => { r.name = el('p-name').value.trim() || t.label; commit(); });
    el('p-type').addEventListener('change', () => { const old = ROOM_TYPES[r.type].label; r.type = el('p-type').value; if (r.name === old) r.name = ROOM_TYPES[r.type].label; commit(); });
    const numField = (id, apply) => el(id).addEventListener('change', () => { const v = parseFloat(el(id).value); if (!isNaN(v)) apply(snap(v)); commit(); });
    numField('p-x', v => { r.x = Math.min(Math.max(0, v), state.plot.w - r.w); });
    numField('p-y', v => { r.y = Math.min(Math.max(0, v), state.plot.h - r.h); });
    numField('p-w', v => { r.w = Math.min(Math.max(2, v), state.plot.w - r.x); });
    numField('p-h', v => { r.h = Math.min(Math.max(2, v), state.plot.h - r.y); });
    body.querySelectorAll('input[data-wall]').forEach(cb => cb.addEventListener('change', () => {
      const side = cb.dataset.wall;
      if (cb.checked) toggleWindow(r, side); else r.windows[side] = 0;
      commit();
    }));
    body.querySelectorAll('input[data-wall-width]').forEach(inp => inp.addEventListener('change', () => {
      const side = inp.dataset.wallWidth;
      const len = side === 'top' || side === 'bottom' ? r.w : r.h;
      let v = parseFloat(inp.value); if (isNaN(v)) v = 0;
      r.windows[side] = Math.max(0, Math.min(len, snap(v)));
      commit();
    }));
    if (t.needsVent) el('p-exhaust').addEventListener('change', () => { r.exhaust = el('p-exhaust').checked; commit(); });
    el('p-delete').addEventListener('click', () => deleteRoom(r.id));
    el('p-duplicate').addEventListener('click', () => {
      const copy = JSON.parse(JSON.stringify(r));
      copy.id = nextId++; copy.name = r.name + ' copy';
      const [x, y] = findFreeSpot(copy.w, copy.h); copy.x = x; copy.y = y;
      state.rooms.push(copy); state.selectedId = copy.id; commit();
    });
  }

  function updatePropsPositions() {
    const r = state.rooms.find(x => x.id === state.selectedId);
    if (!r || !el('p-x')) return;
    el('p-x').value = r.x; el('p-y').value = r.y; el('p-w').value = r.w; el('p-h').value = r.h;
  }

  function gaugeColor(score) { return score == null ? '#9ca3af' : score >= 80 ? '#16a34a' : score >= 55 ? '#d97706' : '#dc2626'; }

  function renderGauges() {
    const g = el('gauges');
    const items = [['overall', 'Overall compliance', true], ['vaastu', 'Vaastu', false], ['ventilation', 'Ventilation', false], ['layout', 'Layout', false]];
    g.innerHTML = items.map(([key, title, big]) => {
      const s = result.scores[key];
      const r = 44, c = 2 * Math.PI * r;
      const pct = s.score == null ? 0 : s.score;
      const off = c * (1 - pct / 100);
      return `
        <div class="gauge ${big ? 'big' : ''}" title="${title}">
          <svg viewBox="0 0 110 110" role="img" aria-label="${title} ${s.score == null ? 'not applicable' : s.score + ' percent'}">
            <circle class="track" cx="55" cy="55" r="${r}"></circle>
            <circle class="bar" cx="55" cy="55" r="${r}" stroke="${gaugeColor(s.score)}" stroke-dasharray="${c}" stroke-dashoffset="${off}" transform="rotate(-90 55 55)"></circle>
            <text class="value" x="55" y="53" text-anchor="middle" dominant-baseline="middle">${s.score == null ? '–' : s.score + '%'}</text>
            <text class="unit" x="55" y="74" text-anchor="middle">compliant</text>
          </svg>
          <div class="title">${title}</div>
          <div class="counts"><b class="p">${s.counts.pass}</b> pass · <b class="w">${s.counts.warn}</b> warn · <b class="f">${s.counts.fail}</b> fail</div>
        </div>`;
    }).join('');
  }

  function renderIssues() {
    const ul = el('issues');
    const order = { fail: 0, warn: 1, pass: 2 };
    let list = result.checks.filter(c => state.filter === 'all' || c.cat === state.filter);
    if (state.hidePass) list = list.filter(c => c.status !== 'pass');
    list = list.slice().sort((a, b) => order[a.status] - order[b.status] || b.weight - a.weight);
    if (!list.length) { ul.innerHTML = `<li class="empty">${state.rooms.length ? 'No warnings or failures here. Everything in this view passes.' : 'Add rooms from the palette to start checking.'}</li>`; return; }
    ul.innerHTML = list.map(c => `<li data-room="${c.roomId == null ? '' : c.roomId}" class="${c.roomId != null && c.roomId === state.selectedId ? 'selected' : ''}"><span class="dot ${c.status}"></span><span><span class="cat">${c.cat}</span>${escapeHtml(c.msg)}</span></li>`).join('');
    ul.querySelectorAll('li[data-room]').forEach(li => li.addEventListener('click', () => {
      const id = li.dataset.room ? parseInt(li.dataset.room, 10) : null;
      state.selectedId = id;
      refresh(true);
    }));
  }

  function escapeHtml(s) { return String(s).replace(/[&<>"']/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch])); }

  // ---------------------------------------------------------------------------
  // Refresh / persistence
  // ---------------------------------------------------------------------------
  let rafPending = false;
  function refresh(full) {
    result = evaluate(state);
    if (full) { renderGauges(); renderIssues(); renderProps(); draw(); return; }
    if (rafPending) return;
    rafPending = true;
    requestAnimationFrame(() => { rafPending = false; renderGauges(); renderIssues(); updatePropsPositions(); draw(); });
  }

  function persist() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(serialize())); } catch (e) { /* storage unavailable */ }
  }
  function serialize() { return { version: 1, plot: state.plot, rooms: state.rooms, showZones: state.showZones, showGrid: state.showGrid }; }
  function restore(data) {
    if (!data || !data.plot || !Array.isArray(data.rooms)) throw new Error('Not a plan file');
    const p = data.plot;
    state.plot = {
      w: clampNum(p.w, 8, 200, 32), h: clampNum(p.h, 8, 200, 36),
      north: SIDES.includes(p.north) ? p.north : 'top',
      open: { top: !!(p.open && p.open.top), right: !!(p.open && p.open.right), bottom: !!(p.open && p.open.bottom), left: !!(p.open && p.open.left) },
      ventRatio: clampNum(p.ventRatio, 1, 50, 10), windowHeight: clampNum(p.windowHeight, 1, 10, 4),
    };
    nextId = 1;
    state.rooms = data.rooms.filter(r => r && ROOM_TYPES[r.type]).map(r => ({
      id: nextId++, type: r.type, name: String(r.name || ROOM_TYPES[r.type].label).slice(0, 40),
      x: clampNum(r.x, 0, state.plot.w, 0), y: clampNum(r.y, 0, state.plot.h, 0),
      w: clampNum(r.w, 1, state.plot.w, 4), h: clampNum(r.h, 1, state.plot.h, 4),
      windows: { top: clampNum(r.windows && r.windows.top, 0, 200, 0), right: clampNum(r.windows && r.windows.right, 0, 200, 0), bottom: clampNum(r.windows && r.windows.bottom, 0, 200, 0), left: clampNum(r.windows && r.windows.left, 0, 200, 0) },
      exhaust: !!r.exhaust,
    }));
    state.showZones = data.showZones !== false;
    state.showGrid = data.showGrid !== false;
    state.selectedId = null;
  }
  function clampNum(v, min, max, dflt) { const n = parseFloat(v); return isNaN(n) ? dflt : Math.min(max, Math.max(min, n)); }

  function bindTopbar() {
    el('btn-sample').addEventListener('click', () => { loadSample(); syncPlotInputs(); resizeCanvas(); refresh(true); persist(); });
    el('btn-clear').addEventListener('click', () => {
      if (state.rooms.length && !window.confirm('Start a new empty plan? The current rooms will be removed.')) return;
      state.rooms = []; state.selectedId = null; refresh(true); persist();
    });
    el('btn-export').addEventListener('click', () => {
      const blob = new Blob([JSON.stringify(serialize(), null, 2)], { type: 'application/json' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob); a.download = 'apartment-plan.json'; a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    });
    el('file-import').addEventListener('change', () => {
      const file = el('file-import').files[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => {
        try { restore(JSON.parse(reader.result)); syncPlotInputs(); resizeCanvas(); refresh(true); persist(); }
        catch (err) { window.alert('Could not import that file: ' + err.message); }
        el('file-import').value = '';
      };
      reader.readAsText(file);
    });
    el('filters').querySelectorAll('button[data-filter]').forEach(b => b.addEventListener('click', () => {
      state.filter = b.dataset.filter;
      el('filters').querySelectorAll('button').forEach(x => x.classList.toggle('active', x === b));
      renderIssues();
    }));
    el('hide-pass').addEventListener('change', () => { state.hidePass = el('hide-pass').checked; renderIssues(); });
  }

  // ---------------------------------------------------------------------------
  // Boot
  // ---------------------------------------------------------------------------
  function init() {
    buildPalette();
    bindPlotInputs();
    bindTopbar();
    let restored = false;
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) { restore(JSON.parse(raw)); restored = true; }
    } catch (e) { restored = false; }
    if (!restored) loadSample();
    syncPlotInputs();
    resizeCanvas();
    refresh(true);
    window.addEventListener('resize', () => { resizeCanvas(); draw(); });
  }

  window.ApartmentPlanner = { state, evaluate, ROOM_TYPES, zoneOfPoint, dirFromVec };
  init();
})();
