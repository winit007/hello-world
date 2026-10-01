/* =====================================================================
   DENTZEN — WEBSITE EDITOR
   Edits assets/js/content.js (photos, reviews, ratings, counters) and
   saves it, plus any uploaded photos, to the GitHub repository that
   hosts the website. Nothing is sent anywhere except api.github.com.
   ===================================================================== */
(function () {
  'use strict';

  var CONTENT_PATH = 'assets/js/content.js';
  var STORE_KEY = 'dentzen-editor';
  var SERVICES = window.SERVICES || [];

  var state = normalise(clone(window.SITE_CONTENT || {}));
  var pending = {};      // new photo path -> base64 JPEG (uploaded on publish)
  var previews = {};     // new photo path -> data URL (for thumbnails)
  var dirty = false;
  var gh = { owner: '', repo: '', branch: '', token: '', sha: null, connected: false };

  function $(sel, ctx) { return (ctx || document).querySelector(sel); }
  function $$(sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function clone(o) { return JSON.parse(JSON.stringify(o)); }

  function normalise(c) {
    c.aboutPhoto = c.aboutPhoto || '';
    c.doctorPhoto = c.doctorPhoto || '';
    c.ratings = c.ratings || {};
    c.stats = c.stats || [];
    c.reviews = c.reviews || [];
    c.beforeAfter = c.beforeAfter || [];
    c.gallery = c.gallery || [];
    c.treatmentPhotos = c.treatmentPhotos || {};
    return c;
  }

  /* ---------- small UI helpers ---------- */
  var toastTimer = null;
  function toast(msg, isErr) {
    var t = $('#toast');
    t.textContent = msg; t.className = 'adm-toast' + (isErr ? ' err' : ''); t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.hidden = true; }, isErr ? 8000 : 5000);
  }
  function markDirty() {
    dirty = true;
    $('#saveBar').hidden = false;
    var m = $('#saveMsg'); m.className = ''; m.innerHTML = '<i class="fa-solid fa-circle-exclamation"></i> You have unsaved changes';
  }
  function src(path) { return previews[path] || path; }
  function thumb(path, icon) {
    return '<div class="adm-thumb">' + (path ? '<img src="' + esc(src(path)) + '" alt="">' + (pending[path] ? '<span class="adm-new">New</span>' : '') : '<i class="' + (icon || 'fa-regular fa-image') + '"></i>') + '</div>';
  }
  window.addEventListener('beforeunload', function (e) { if (dirty) { e.preventDefault(); e.returnValue = ''; } });

  /* ---------- photos: pick, resize, queue for upload ---------- */
  function slug(name) { return String(name).toLowerCase().replace(/\.[a-z0-9]+$/, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 40) || 'photo'; }
  function stamp() { var d = new Date(), p = function (n) { return (n < 10 ? '0' : '') + n; }; return d.getFullYear() + p(d.getMonth() + 1) + p(d.getDate()) + '-' + p(d.getHours()) + p(d.getMinutes()) + p(d.getSeconds()); }
  var seq = 0;
  function processImage(file, maxSize) {
    return new Promise(function (resolve, reject) {
      if (!/^image\//.test(file.type)) { reject(new Error(file.name + ' is not a photo.')); return; }
      var reader = new FileReader();
      reader.onerror = function () { reject(new Error('Could not read ' + file.name)); };
      reader.onload = function () {
        var img = new Image();
        img.onerror = function () { reject(new Error('Could not open ' + file.name)); };
        img.onload = function () {
          var scale = Math.min(1, maxSize / Math.max(img.width, img.height));
          var w = Math.round(img.width * scale), h = Math.round(img.height * scale);
          var canvas = document.createElement('canvas'); canvas.width = w; canvas.height = h;
          var ctx = canvas.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, w, h); ctx.drawImage(img, 0, 0, w, h);
          var dataUrl = canvas.toDataURL('image/jpeg', 0.85);
          var path = 'images/uploads/' + stamp() + '-' + (++seq) + '-' + slug(file.name) + '.jpg';
          pending[path] = dataUrl.split(',')[1];
          previews[path] = dataUrl;
          resolve(path);
        };
        img.src = reader.result;
      };
      reader.readAsDataURL(file);
    });
  }
  function pickPhotos(multiple, maxSize, onPath) {
    var input = document.createElement('input');
    input.type = 'file'; input.accept = 'image/*'; input.multiple = !!multiple;
    input.addEventListener('change', function () {
      var files = Array.prototype.slice.call(input.files || []);
      Promise.all(files.map(function (f) { return processImage(f, maxSize || 1600).then(onPath); }))
        .then(function () { if (files.length) { markDirty(); renderAll(); } })
        .catch(function (err) { toast(err.message, true); });
    });
    input.click();
  }
  function forget(path) { if (pending[path]) { delete pending[path]; delete previews[path]; } }

  /* ---------- panels ---------- */
  function renderPhotos() {
    var single = function (key, title, help) {
      var path = state[key];
      return '<div class="adm-photo">' + thumb(path) + '<div class="adm-photo-body"><strong>' + title + '</strong><small>' + help + '</small>' +
        '<div class="adm-actions"><button class="adm-btn primary" data-act="set-photo" data-key="' + key + '"><i class="fa-solid fa-upload"></i> ' + (path ? 'Replace' : 'Upload') + '</button>' +
        (path ? '<button class="adm-btn danger" data-act="clear-photo" data-key="' + key + '"><i class="fa-regular fa-trash-can"></i> Remove</button>' : '') + '</div></div></div>';
    };
    var gallery = state.gallery.map(function (g, i) {
      return '<div class="adm-photo">' + thumb(g.src) + '<div class="adm-photo-body"><label>Caption<input class="adm-input" data-bind="gallery.' + i + '.caption" value="' + esc(g.caption) + '"></label>' +
        '<div class="adm-actions"><button class="adm-btn danger" data-act="del-gallery" data-i="' + i + '"><i class="fa-regular fa-trash-can"></i> Remove</button></div></div></div>';
    }).join('');
    var treatments = SERVICES.map(function (s) {
      var custom = state.treatmentPhotos[s.id];
      return '<div class="adm-trow">' + thumb(custom || s.img) + '<div><strong>' + esc(s.name) + '</strong><br><small class="adm-hint">' + (custom ? 'Your photo' : 'Default picture') + '</small></div>' +
        '<div class="adm-actions"><button class="adm-btn" data-act="set-treatment" data-id="' + s.id + '"><i class="fa-solid fa-upload"></i> Upload</button>' +
        (custom ? '<button class="adm-btn danger" data-act="reset-treatment" data-id="' + s.id + '">Use default</button>' : '') + '</div></div>';
    }).join('');
    $('#tab-photos').innerHTML =
      '<h2>Main photos</h2><p class="adm-hint">Photos are resized automatically, so you can upload straight from your phone.</p>' +
      '<div class="adm-photo-row">' +
        single('aboutPhoto', 'About section photo', 'Shown next to “Trusted &amp; Best Dentist in Patna”. A photo of you or the clinic works well.') +
        single('doctorPhoto', 'Dr. Wagisha’s photo', 'Shown in “Meet Dr. W. Wagisha”. A portrait (taller than wide) looks best.') +
      '</div>' +
      '<h2>Our Clinic gallery</h2><p class="adm-hint">Photos of your clinic: entrance, reception, treatment rooms, equipment.</p>' +
      '<div class="adm-photo-row">' + gallery + '</div>' +
      '<div class="adm-actions" style="margin-top:14px"><button class="adm-btn primary" data-act="add-gallery"><i class="fa-solid fa-plus"></i> Add clinic photos</button></div>' +
      '<h2>Treatment pictures</h2><p class="adm-hint">Replace the picture shown on any treatment card.</p>' + treatments;
  }

  function renderBA() {
    var items = state.beforeAfter.map(function (c, i) {
      var slot = function (side) {
        var path = c[side];
        return '<div class="adm-photo">' + thumb(path, 'fa-regular fa-images') + '<div class="adm-photo-body"><strong>' + (side === 'before' ? 'Before' : 'After') + '</strong>' +
          '<div class="adm-actions"><button class="adm-btn primary" data-act="set-ba" data-i="' + i + '" data-side="' + side + '"><i class="fa-solid fa-upload"></i> ' + (path ? 'Replace' : 'Upload') + '</button>' +
          (path ? '<button class="adm-btn danger" data-act="clear-ba" data-i="' + i + '" data-side="' + side + '">Remove</button>' : '') + '</div></div></div>';
      };
      return '<div class="adm-item"><div class="adm-item-head"><label style="flex:1">Case title<input class="adm-input" data-bind="beforeAfter.' + i + '.title" value="' + esc(c.title) + '"></label>' +
        '<button class="adm-btn danger" data-act="del-ba" data-i="' + i + '"><i class="fa-regular fa-trash-can"></i> Delete case</button></div>' +
        '<div class="adm-ba">' + slot('before') + slot('after') + '</div></div>';
    }).join('');
    $('#tab-ba').innerHTML = '<h2>Before &amp; After cases</h2><p class="adm-hint">Upload a “before” and an “after” photo for each case. Visitors drag a slider to compare them. Cases without both photos show “coming soon”. Please only use photos your patients have agreed to share.</p>' +
      '<div class="adm-list">' + (items || '<p class="adm-empty">No cases yet.</p>') + '</div>' +
      '<div class="adm-actions" style="margin-top:14px"><button class="adm-btn primary" data-act="add-ba"><i class="fa-solid fa-plus"></i> Add a case</button></div>';
  }

  function renderReviews() {
    var sources = ['Google', 'Justdial', 'Practo'];
    var items = state.reviews.map(function (r, i) {
      return '<div class="adm-item"><div class="adm-grid2">' +
        '<label>Patient name<input class="adm-input" data-bind="reviews.' + i + '.name" value="' + esc(r.name) + '"></label>' +
        '<label>From<select class="adm-input" data-bind="reviews.' + i + '.source">' + sources.map(function (s) { return '<option' + (r.source === s ? ' selected' : '') + '>' + s + '</option>'; }).join('') + '</select></label>' +
        '<label>Stars<select class="adm-input" data-bind="reviews.' + i + '.rating" data-type="number">' + [5, 4, 3, 2, 1].map(function (n) { return '<option value="' + n + '"' + (+r.rating === n ? ' selected' : '') + '>' + n + ' ★</option>'; }).join('') + '</select></label>' +
        '<label>Date (optional)<input class="adm-input" data-bind="reviews.' + i + '.date" value="' + esc(r.date) + '" placeholder="e.g. March 2026"></label></div>' +
        '<label>Review text<textarea class="adm-input" data-bind="reviews.' + i + '.text">' + esc(r.text) + '</textarea></label>' +
        '<div class="adm-actions"><button class="adm-btn danger" data-act="del-review" data-i="' + i + '"><i class="fa-regular fa-trash-can"></i> Delete review</button></div></div>';
    }).join('');
    $('#tab-reviews').innerHTML = '<h2>Patient reviews</h2><p class="adm-hint">Copy reviews from your Google Business profile or Justdial page and paste them here. They appear in a slider in the “Patients Love Us” section.</p>' +
      '<div class="adm-actions" style="margin-bottom:14px"><button class="adm-btn primary" data-act="add-review"><i class="fa-solid fa-plus"></i> Add a review</button></div>' +
      '<div class="adm-list">' + (items || '<p class="adm-empty">No reviews yet. Press “Add a review” to add your first one.</p>') + '</div>';
  }

  function renderNumbers() {
    var r = state.ratings;
    var field = function (label, key, ph) {
      return '<label>' + label + '<input class="adm-input" type="number" step="any" min="0" data-bind="ratings.' + key + '" data-type="number" value="' + esc(r[key] == null ? '' : r[key]) + '" placeholder="' + (ph || '') + '"></label>';
    };
    var rows = state.stats.map(function (s, i) {
      var auto = s.value === 'years';
      return '<div class="adm-crow"><label>Label<input class="adm-input" data-bind="stats.' + i + '.label" value="' + esc(s.label) + '"></label>' +
        '<label>Number' + (auto ? '<input class="adm-input" value="Automatic" disabled>' : '<input class="adm-input" type="number" step="any" min="0" data-bind="stats.' + i + '.value" data-type="number" value="' + esc(s.value == null ? '' : s.value) + '" placeholder="hidden">') + '</label>' +
        '<label>After the number<input class="adm-input" data-bind="stats.' + i + '.suffix" value="' + esc(s.suffix) + '"></label>' +
        '<button class="adm-btn danger" data-act="del-stat" data-i="' + i + '" aria-label="Delete counter"><i class="fa-regular fa-trash-can"></i></button></div>';
    }).join('');
    $('#tab-numbers').innerHTML = '<h2>Ratings</h2><p class="adm-hint">Shown on the rating cards in the reviews section.</p>' +
      '<div class="adm-grid2">' + field('Google rating (out of 5)', 'google', '4.9') + field('Number of Google reviews', 'googleCount', 'optional') +
      field('Justdial rating (out of 5)', 'justdial', '4.9') + field('Number of Justdial ratings', 'justdialCount', 'optional') + field('Practo recommendation %', 'practo', '90') + '</div>' +
      '<h2>Counters</h2><p class="adm-hint">The green band of numbers below the slider. Leave a number empty to hide that counter.</p>' +
      '<div class="adm-counters">' + rows + '</div>' +
      '<div class="adm-actions" style="margin-top:14px"><button class="adm-btn primary" data-act="add-stat"><i class="fa-solid fa-plus"></i> Add a counter</button></div>';
  }

  function renderAll() { renderPhotos(); renderBA(); renderReviews(); renderNumbers(); }

  /* ---------- editing ---------- */
  function setPath(obj, path, value) {
    var keys = path.split('.'), o = obj;
    for (var i = 0; i < keys.length - 1; i++) o = o[keys[i]];
    o[keys[keys.length - 1]] = value;
  }
  document.addEventListener('input', function (e) {
    var el = e.target.closest('[data-bind]'); if (!el) return;
    var v = el.value;
    if (el.getAttribute('data-type') === 'number') v = v === '' ? null : Number(v);
    setPath(state, el.getAttribute('data-bind'), v);
    markDirty();
  });
  document.addEventListener('change', function (e) { if (e.target.matches('select[data-bind]')) e.target.dispatchEvent(new Event('input', { bubbles: true })); });

  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-act]'); if (!b) return;
    var act = b.getAttribute('data-act'), i = +b.getAttribute('data-i'), key = b.getAttribute('data-key'), id = b.getAttribute('data-id'), side = b.getAttribute('data-side');
    var changed = true;
    switch (act) {
      case 'set-photo': pickPhotos(false, 1600, function (p) { forget(state[key]); state[key] = p; }); changed = false; break;
      case 'clear-photo': forget(state[key]); state[key] = ''; break;
      case 'add-gallery': pickPhotos(true, 1600, function (p) { state.gallery.push({ src: p, caption: '' }); }); changed = false; break;
      case 'del-gallery': forget(state.gallery[i].src); state.gallery.splice(i, 1); break;
      case 'set-treatment': pickPhotos(false, 1200, function (p) { forget(state.treatmentPhotos[id]); state.treatmentPhotos[id] = p; }); changed = false; break;
      case 'reset-treatment': forget(state.treatmentPhotos[id]); delete state.treatmentPhotos[id]; break;
      case 'set-ba': pickPhotos(false, 1400, function (p) { forget(state.beforeAfter[i][side]); state.beforeAfter[i][side] = p; }); changed = false; break;
      case 'clear-ba': forget(state.beforeAfter[i][side]); state.beforeAfter[i][side] = ''; break;
      case 'add-ba': state.beforeAfter.push({ title: 'New case', before: '', after: '' }); break;
      case 'del-ba':
        if (!confirm('Delete the case “' + state.beforeAfter[i].title + '”?')) return;
        forget(state.beforeAfter[i].before); forget(state.beforeAfter[i].after); state.beforeAfter.splice(i, 1); break;
      case 'add-review': state.reviews.unshift({ name: '', source: 'Google', rating: 5, date: '', text: '' }); break;
      case 'del-review': if (!confirm('Delete this review?')) return; state.reviews.splice(i, 1); break;
      case 'add-stat': state.stats.push({ icon: 'fa-tooth', value: null, suffix: '+', label: '' }); break;
      case 'del-stat': state.stats.splice(i, 1); break;
      default: changed = false;
    }
    if (changed) { markDirty(); renderAll(); }
  });

  $$('.adm-tabs [data-tab]').forEach(function (t) {
    t.addEventListener('click', function () {
      $$('.adm-tabs [data-tab]').forEach(function (x) { x.setAttribute('aria-selected', x === t ? 'true' : 'false'); });
      $$('.adm-panel').forEach(function (p) { p.hidden = p.id !== 'tab-' + t.getAttribute('data-tab'); });
    });
  });

  /* ---------- GitHub ---------- */
  function b64encode(str) {
    var bytes = new TextEncoder().encode(str), bin = '';
    for (var i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(bin);
  }
  function b64decode(b64) {
    var bin = atob(String(b64).replace(/\s/g, '')), bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder().decode(bytes);
  }
  function serialise() {
    return '/* =====================================================================\n' +
      '   DENTZEN — EDITABLE CONTENT (photos, reviews, ratings, counters)\n' +
      '   This file is rewritten by the admin page (admin.html). You can also\n' +
      '   edit it by hand: keep it valid JSON after the "=" sign.\n' +
      '   ===================================================================== */\n' +
      'window.SITE_CONTENT = ' + JSON.stringify(state, null, 2) + ';\n';
  }
  function parseContent(text) {
    var start = text.indexOf('{', text.indexOf('SITE_CONTENT')), end = text.lastIndexOf('}');
    return normalise(JSON.parse(text.slice(start, end + 1)));
  }
  function api(method, sub, body) {
    return fetch('https://api.github.com/repos/' + encodeURIComponent(gh.owner) + '/' + encodeURIComponent(gh.repo) + sub, {
      method: method,
      headers: { 'Accept': 'application/vnd.github+json', 'Authorization': 'Bearer ' + gh.token, 'X-GitHub-Api-Version': '2022-11-28' },
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (r.ok) return data;
        var msg = {
          401: 'GitHub rejected the access token. Check that it is copied correctly and has not expired.',
          403: 'The token does not have permission to change this repository. Give it “Contents: Read and write”.',
          404: 'Could not find the repository, branch or website files. Check the account, repository and branch names.',
          409: 'The website was changed somewhere else at the same time. Reload this page and try again.',
          422: 'GitHub could not save the file. Check the branch name and try again.'
        }[r.status] || ('GitHub error ' + r.status + (data.message ? ': ' + data.message : ''));
        var err = new Error(msg); err.status = r.status; throw err;
      });
    });
  }
  function encodePath(p) { return p.split('/').map(encodeURIComponent).join('/'); }
  function setStatus(kind, html) { var s = $('#connStatus'); s.className = 'adm-status' + (kind ? ' ' + kind : ''); s.innerHTML = html; }

  function connect() {
    gh.owner = $('#ghOwner').value.trim(); gh.repo = $('#ghRepo').value.trim(); gh.branch = $('#ghBranch').value.trim(); gh.token = $('#ghToken').value.trim();
    if (!gh.owner || !gh.repo || !gh.branch || !gh.token) { setStatus('err', '<i class="fa-solid fa-triangle-exclamation"></i> Fill in all four boxes, including the access token.'); return Promise.resolve(); }
    setStatus('', '<i class="fa-solid fa-spinner fa-spin"></i> Connecting…');
    return api('GET', '/contents/' + encodePath(CONTENT_PATH) + '?ref=' + encodeURIComponent(gh.branch)).then(function (file) {
      gh.sha = file.sha; gh.connected = true;
      if (!dirty) { state = parseContent(b64decode(file.content)); renderAll(); }
      try {
        if ($('#ghRemember').checked) localStorage.setItem(STORE_KEY, JSON.stringify({ owner: gh.owner, repo: gh.repo, branch: gh.branch, token: gh.token }));
        else localStorage.removeItem(STORE_KEY);
      } catch (e) { /* storage blocked: stay connected for this visit only */ }
      setStatus('ok', '<i class="fa-solid fa-circle-check"></i> Connected to <strong>' + esc(gh.owner + '/' + gh.repo) + '</strong> (branch <strong>' + esc(gh.branch) + '</strong>). Your changes will be published there.');
      $('#connectBox').open = false; $('#forgetBtn').hidden = false;
    }).catch(function (err) {
      gh.connected = false;
      setStatus('err', '<i class="fa-solid fa-triangle-exclamation"></i> ' + esc(err.message));
    });
  }

  function publish() {
    if (!gh.connected) { $('#connectBox').open = true; $('#connectBox').scrollIntoView({ behavior: 'smooth', block: 'center' }); toast('Connect to GitHub first, then press Publish again.', true); return; }
    var btn = $('#publishBtn'), msg = $('#saveMsg'), paths = Object.keys(pending), n = paths.length, done = 0;
    btn.disabled = true; msg.className = 'busy';
    var chain = Promise.resolve();
    paths.forEach(function (p) {
      chain = chain.then(function () {
        msg.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Uploading photo ' + (++done) + ' of ' + n + '…';
        return api('PUT', '/contents/' + encodePath(p), { message: 'Upload photo ' + p.split('/').pop(), content: pending[p], branch: gh.branch })
          .then(function () { delete pending[p]; });
      });
    });
    chain.then(function () {
      msg.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Saving your changes…';
      return api('PUT', '/contents/' + encodePath(CONTENT_PATH), { message: 'Update website content (photos, reviews, counters)', content: b64encode(serialise()), branch: gh.branch, sha: gh.sha });
    }).then(function (res) {
      gh.sha = res.content && res.content.sha;
      dirty = false; $('#saveBar').hidden = true; renderAll();
      toast('Published! Your website will show the changes in about 1–2 minutes.');
    }).catch(function (err) {
      msg.className = ''; msg.innerHTML = '<i class="fa-solid fa-triangle-exclamation"></i> Not published: ' + esc(err.message);
      toast(err.message, true);
    }).then(function () { btn.disabled = false; });
  }

  $('#connectBtn').addEventListener('click', connect);
  $('#publishBtn').addEventListener('click', publish);
  $('#forgetBtn').addEventListener('click', function () {
    try { localStorage.removeItem(STORE_KEY); } catch (e) { /* ignore */ }
    gh.connected = false; gh.token = ''; $('#ghToken').value = ''; $('#forgetBtn').hidden = true; $('#connectBox').open = true;
    setStatus('', '<i class="fa-solid fa-plug-circle-xmark"></i> Disconnected. The token has been removed from this device.');
  });

  renderAll();
  try {
    var saved = JSON.parse(localStorage.getItem(STORE_KEY) || 'null');
    if (saved && saved.token) {
      $('#ghOwner').value = saved.owner; $('#ghRepo').value = saved.repo; $('#ghBranch').value = saved.branch; $('#ghToken').value = saved.token;
      connect();
    }
  } catch (e) { /* storage blocked */ }
})();
