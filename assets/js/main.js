/* =====================================================================
   DENTZEN DENTAL HOSPITAL — SITE SCRIPTS
   Reads clinic details from config.js and content from data.js.
   ===================================================================== */
(function () {
  'use strict';

  var C = window.CLINIC || {};
  var SC = window.SITE_CONTENT || {};          // photos, reviews, ratings & counters (edited from admin.html)
  var SERVICES = window.SERVICES || [];
  var TP = SC.treatmentPhotos || {};
  SERVICES.forEach(function (s) { if (TP[s.id]) s.img = TP[s.id]; });
  var ICONS = window.ICONS || {};
  var DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  var DAY_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var WEEK_ORDER = [1, 2, 3, 4, 5, 6, 0];
  var reduceMotion = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---------- small helpers ---------- */
  function $(sel, ctx) { return (ctx || document).querySelector(sel); }
  function $$(sel, ctx) { return Array.prototype.slice.call((ctx || document).querySelectorAll(sel)); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function svgIcon(id) { return '<svg viewBox="0 0 48 48" aria-hidden="true">' + (ICONS[id] || ICONS.checkup || '') + '</svg>'; }
  function serviceById(id) { for (var i = 0; i < SERVICES.length; i++) if (SERVICES[i].id === id) return SERVICES[i]; return null; }

  /* Clinic time is always India Standard Time, whatever the visitor's timezone. */
  function istNow() { var d = new Date(); return new Date(d.getTime() + (d.getTimezoneOffset() + 330) * 60000); }
  function isoDate(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }
  function toMin(hhmm) { var p = hhmm.split(':'); return +p[0] * 60 + +p[1]; }
  function fmtTime(min, withMeridiem) {
    var h = Math.floor(min / 60), m = min % 60, mer = h >= 12 ? 'PM' : 'AM', h12 = h % 12 || 12;
    return h12 + (m ? ':' + pad(m) : '') + (withMeridiem === false ? '' : ' ' + mer);
  }
  function fmtRange(a, b) {
    var sa = toMin(a), sb = toMin(b);
    var sameMer = (sa < 720) === (sb < 720);
    return fmtTime(sa, !sameMer) + '–' + fmtTime(sb);
  }
  function sessionsFor(day) { return (C.hours && C.hours[day]) || []; }
  function byAppt(day) { return (C.appointmentOnly || []).indexOf(day) !== -1; }
  function daySummary(day) { var s = sessionsFor(day); return s.length ? s.map(function (r) { return fmtRange(r[0], r[1]); }).join(', ') + (byAppt(day) ? ' (by appointment)' : '') : 'Closed'; }

  /* ---------- contact links ---------- */
  function digits(s) { return String(s || '').replace(/\D/g, ''); }
  function intl(num) { var d = digits(num); if (d.length === 10) d = '91' + d; return d.length >= 11 ? d : ''; }
  var phoneIntl = /x/i.test(C.phone || '') ? '' : intl(C.phone);
  var waIntl = intl(C.whatsapp) || phoneIntl;
  var hasPhone = !!phoneIntl, hasWa = !!waIntl;
  function telHref() { return hasPhone ? 'tel:+' + phoneIntl : '#appointment'; }
  function waHref(text) { return hasWa ? 'https://wa.me/' + waIntl + '?text=' + encodeURIComponent(text || ('Hello ' + C.name + ', I would like to book an appointment.')) : '#appointment'; }

  var now = istNow();
  var years = Math.max(1, now.getFullYear() - ((C.doctor && C.doctor.graduationYear) || now.getFullYear()));
  var addr = C.address || {};
  var addressShort = [addr.line1, addr.area, addr.city].filter(Boolean).join(', ');
  var addressFull = [addr.line1, addr.area, addr.city, [addr.state, addr.pin].filter(Boolean).join(' – ')].filter(Boolean).join(', ');

  function hoursShort() {
    var groups = [], appt = [];
    WEEK_ORDER.forEach(function (d) {
      var s = sessionsFor(d), text = s.length ? s.map(function (r) { return fmtRange(r[0], r[1]); }).join(', ') : 'Closed';
      if (byAppt(d) && s.length) appt.push(DAY_SHORT[d]);
      var last = groups[groups.length - 1];
      if (last && last.text === text) last.days.push(d); else groups.push({ text: text, days: [d] });
    });
    var out = groups.map(function (g) {
      var label = DAY_SHORT[g.days[0]] + (g.days.length > 1 ? '–' + DAY_SHORT[g.days[g.days.length - 1]] : '');
      return label + ' ' + g.text;
    }).join(' · ');
    return appt.length ? out + ' · ' + appt.join(', ') + ' by appointment only' : out;
  }

  /* ---------- 1. Bind config values into the page ---------- */
  function bindConfig() {
    var d = C.doctor || {};
    var values = {
      phone: C.phone, email: C.email, fee: C.consultationFee, years: years,
      addressShort: addressShort, addressFull: addressFull, landmark: addr.landmark, hoursShort: hoursShort(),
      doctorName: d.name, doctorDegree: d.degree, doctorTitle: d.title, doctorCollege: d.college,
      doctorGradYear: d.graduationYear, doctorReg: d.registration
    };
    $$('[data-bind]').forEach(function (el) {
      var v = values[el.getAttribute('data-bind')];
      if (v !== undefined && v !== null && v !== '') el.textContent = v;
    });

    $$('[data-tel]').forEach(function (a) { a.setAttribute('href', telHref()); });
    $$('[data-wa]').forEach(function (a) {
      a.setAttribute('href', waHref());
      if (hasWa) { a.setAttribute('target', '_blank'); a.setAttribute('rel', 'noopener'); }
    });
    $$('[data-mail]').forEach(function (a) { a.setAttribute('href', 'mailto:' + C.email); });
    $$('[data-needs="email"]').forEach(function (el) { el.hidden = !C.email; });
    $$('[data-fee-show]').forEach(function (el) { el.hidden = !C.consultationFee; });

    var social = C.social || {};
    $$('[data-social]').forEach(function (li) {
      var url = social[li.getAttribute('data-social')];
      if (!url) { li.hidden = true; return; }
      $('a', li).setAttribute('href', url);
    });
    $$('[data-social-link]').forEach(function (a) {
      var url = social[a.getAttribute('data-social-link')];
      if (!url) a.hidden = true; else a.setAttribute('href', url);
    });

    if (SC.doctorPhoto) {
      var box = $('#doctorPhoto .doc-placeholder');
      if (box) {
        var img = document.createElement('img');
        img.src = SC.doctorPhoto; img.alt = d.name + ', ' + d.degree; img.loading = 'lazy';
        box.replaceWith(img);
      }
    }
    if (SC.aboutPhoto) {
      var slot = $('#aboutPhoto');
      if (slot) slot.innerHTML = '<img class="about-photo" src="' + esc(SC.aboutPhoto) + '" alt="DENTZEN Dental Hospital, Kankarbagh, Patna" loading="lazy">';
    }

    var map = $('#mapFrame');
    if (map && C.mapQuery) map.src = 'https://maps.google.com/maps?q=' + encodeURIComponent(C.mapQuery) + '&z=16&output=embed';
    var dir = $('#directionsBtn');
    if (dir) dir.href = 'https://www.google.com/maps/dir/?api=1&destination=' + encodeURIComponent(C.mapQuery || addressFull);

    var y = $('#year'); if (y) y.textContent = now.getFullYear();

    /* Patient Safety section: shown only once the clinic has verified every step */
    var ic = C.infectionControl || {}, safety = $('#safety');
    if (safety) {
      safety.hidden = !ic.verified;
      $$('[data-safety-link]').forEach(function (a) { a.hidden = !ic.verified; });
      var ch = ic.chemical || {}, box = $('#chemDetails');
      if (box && ch.product) {
        box.hidden = false;
        box.innerHTML = '<strong>What we use:</strong> ' + esc(ch.product) + (ch.concentration ? ' at ' + esc(ch.concentration) : '') +
          (ch.contactTime ? ', contact time ' + esc(ch.contactTime) : '') + (ch.usedFor ? ' — ' + esc(ch.usedFor) : '') + '.';
      }
    }
  }

  /* ---------- 2. Live open / closed status + hours table ---------- */
  function openStatus() {
    var t = istNow(), day = t.getDay(), mins = t.getHours() * 60 + t.getMinutes();
    var today = sessionsFor(day);
    for (var i = 0; i < today.length; i++) {
      var a = toMin(today[i][0]), b = toMin(today[i][1]);
      if (mins >= a && mins < b) return byAppt(day) ? { open: true, text: 'By appointment today · until ' + fmtTime(b) } : { open: true, text: 'Open now · until ' + fmtTime(b) };
      if (mins < a) return { open: false, text: 'Closed · opens ' + fmtTime(a) };
    }
    for (var k = 1; k <= 7; k++) {
      var nd = (day + k) % 7, s = sessionsFor(nd);
      if (s.length) return { open: false, text: 'Closed · opens ' + (k === 1 ? 'tomorrow' : DAY_SHORT[nd]) + ' ' + fmtTime(toMin(s[0][0])) };
    }
    return { open: false, text: 'Closed' };
  }
  function renderStatus() {
    var st = openStatus();
    $$('[data-open-status]').forEach(function (el) {
      el.classList.toggle('is-open', st.open);
      el.classList.toggle('is-closed', !st.open);
      $('.txt', el).textContent = st.text;
    });
  }
  function renderHours() {
    var table = $('#hoursTable'); if (!table) return;
    var today = istNow().getDay();
    table.innerHTML = '<tbody>' + WEEK_ORDER.map(function (d) {
      return '<tr' + (d === today ? ' class="today"' : '') + '><td>' + DAY_NAMES[d] + (d === today ? ' (today)' : '') + '</td><td>' + esc(daySummary(d)) + '</td></tr>';
    }).join('') + '</tbody>';
  }

  /* ---------- 3. Stats counters ---------- */
  function renderStats() {
    var grid = $('#statsGrid'); if (!grid) return;
    grid.innerHTML = (SC.stats || []).filter(function (s) {
      return s.value !== null && s.value !== undefined && s.value !== '';   // a counter without a number stays hidden
    }).map(function (s) {
      var val = s.value === 'years' ? years : s.value;
      var ic = s.badge ? '<b class="stat-badge' + (s.badge.length > 3 ? ' sm' : '') + '">' + esc(s.badge) + '</b>'
        : s.svg ? svgIcon(s.svg)
        : '<i class="' + (/\bfa-(solid|brands|regular)\b/.test(s.icon || '') ? '' : 'fa-solid ') + esc(s.icon || 'fa-tooth') + '"></i>';
      return '<div class="stat"><span class="stat-ic' + (s.svg ? ' is-svg' : '') + '">' + ic + '</span><div>' +
        '<div class="stat-num" data-count="' + esc(val) + '" data-plain="' + (s.plain ? 1 : 0) + '" data-prefix="' + esc(s.prefix || '') + '" data-suffix="' + esc(s.suffix || '') + '">' +
        esc((s.prefix || '') + fmtNum(val, s.plain) + (s.suffix || '')) + '</div>' +
        '<div class="stat-label">' + esc(s.label) + '</div></div></div>';
    }).join('');
    var nums = $$('.stat-num', grid);
    if (reduceMotion || !('IntersectionObserver' in window)) return;
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (!e.isIntersecting) return;
        io.unobserve(e.target);
        animateCount(e.target);
      });
    }, { threshold: .5 });
    nums.forEach(function (n) { io.observe(n); });
  }
  function decimals(n) { return (String(n).split('.')[1] || '').length; }
  function fmtNum(n, plain) {
    if (plain) return String(n);
    var d = decimals(n);
    return Number(n).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  function animateCount(el) {
    var raw = el.getAttribute('data-count'), target = +raw, plain = el.getAttribute('data-plain') === '1', dec = decimals(raw);
    var pre = el.getAttribute('data-prefix'), suf = el.getAttribute('data-suffix');
    var from = plain ? Math.max(0, target - 40) : 0, start = null, dur = 1600;
    function step(ts) {
      if (!start) start = ts;
      var p = Math.min(1, (ts - start) / dur), eased = 1 - Math.pow(1 - p, 3);
      var v = from + (target - from) * eased;
      v = dec ? +v.toFixed(dec) : Math.round(v);
      el.textContent = pre + (plain ? v : fmtNum(dec ? v.toFixed(dec) : v)) + suf;
      if (p < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
  }

  /* ---------- 4. Services grid, menu, footer list ---------- */
  function renderServices() {
    var grid = $('#servicesGrid');
    if (grid) grid.innerHTML = SERVICES.map(function (s) {
      return '<article class="svc-card reveal"><div class="svc-media">' +
        (s.img ? '<img src="' + esc(s.img) + '" alt="' + esc(s.name) + '" loading="lazy">' : '') +
        '<span class="svc-ic">' + svgIcon(s.id) + '</span></div>' +
        '<div class="svc-body"><h3>' + esc(s.name) + '</h3><p>' + esc(s.short) + '</p>' +
        '<div class="svc-actions"><button type="button" class="svc-more" data-service="' + s.id + '">Read More</button>' +
        '<a href="#appointment" class="svc-book" data-book="' + s.id + '">Book <i class="fa-solid fa-arrow-right"></i></a></div></div></article>';
    }).join('');

    var dd = $('#servicesDropdown');
    if (dd) dd.innerHTML = '<li><a href="#services">' + svgIcon('checkup') + ' All treatments</a></li>' + SERVICES.map(function (s) {
      return '<li><a href="#services" data-service="' + s.id + '">' + svgIcon(s.id) + esc(s.name) + '</a></li>';
    }).join('');
    $$('#servicesDropdown svg').forEach(function (svg) { svg.setAttribute('fill', 'none'); svg.setAttribute('stroke', 'currentColor'); svg.setAttribute('stroke-width', '2.4'); svg.setAttribute('stroke-linecap', 'round'); svg.setAttribute('stroke-linejoin', 'round'); });

    var fl = $('#footerServices');
    if (fl) fl.innerHTML = SERVICES.slice(0, 9).map(function (s) {
      return '<li><button type="button" data-service="' + s.id + '">' + esc(s.name) + '</button></li>';
    }).join('');

    var sel = $('#fService');
    if (sel) {
      sel.innerHTML = '<option value="General consultation">General consultation / check-up</option>' +
        SERVICES.filter(function (s) { return s.id !== 'checkup'; }).map(function (s) { return '<option value="' + esc(s.name) + '">' + esc(s.name) + '</option>'; }).join('') +
        '<option value="Dental emergency">Dental emergency (pain / swelling)</option>';
    }
  }

  /* ---------- 5. Service detail modal ---------- */
  var modal = $('#serviceModal'), lastFocus = null;
  function openService(id) {
    var s = serviceById(id); if (!s || !modal) return;
    var list = function (arr) { return '<ul>' + (arr || []).map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>'; };
    $('#modalBody').innerHTML =
      (s.img ? '<img class="m-photo" src="' + esc(s.img) + '" alt="' + esc(s.name) + '">' : '') +
      '<div class="m-head"><span class="svc-ic">' + svgIcon(s.id) + '</span><h2 id="modalTitle">' + esc(s.name) + '</h2></div>' +
      '<div class="m-content"><p>' + esc(s.about) + '</p>' +
      '<span class="m-duration"><i class="fa-regular fa-clock"></i> ' + esc(s.duration) + '</span>' +
      '<div class="m-grid">' +
        '<div class="m-block"><h3><i class="fa-solid fa-circle-question"></i> You may need this if</h3>' + list(s.signs) + '</div>' +
        '<div class="m-block"><h3><i class="fa-solid fa-list-check"></i> What happens</h3><ol>' + (s.steps || []).map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ol></div>' +
        '<div class="m-block"><h3><i class="fa-solid fa-heart-pulse"></i> Aftercare</h3>' + list(s.care) + '</div>' +
        '<div class="m-block"><h3><i class="fa-solid fa-indian-rupee-sign"></i> Cost</h3><ul><li>Depends on your teeth and the materials chosen</li><li>You get a clear, written estimate after your examination' + (C.consultationFee ? ' (consultation ₹' + esc(C.consultationFee) + ')' : '') + '</li></ul></div>' +
      '</div>' +
      '<div class="m-foot"><a href="#appointment" class="btn btn-accent" data-book="' + s.id + '" data-close><i class="fa-regular fa-calendar-check"></i> Book this treatment</a>' +
      '<a class="btn btn-outline-wa" ' + (hasWa ? 'target="_blank" rel="noopener" ' : 'data-close ') + 'href="' + esc(waHref('Hello ' + C.name + ', I would like to know more about ' + s.name + '.')) + '"><i class="fa-brands fa-whatsapp"></i> Ask on WhatsApp</a></div>' +
      '<p class="m-disclaimer">General information only — your exact treatment plan is decided after an examination.</p></div>';
    lastFocus = document.activeElement;
    modal.hidden = false;
    document.body.classList.add('modal-open');
    $('.modal-close', modal).focus();
  }
  function closeModal() {
    if (!modal || modal.hidden) return;
    modal.hidden = true;
    document.body.classList.remove('modal-open');
    if (lastFocus && lastFocus.focus) lastFocus.focus({ preventScroll: true });
  }

  /* ---------- 6. Treatment finder ---------- */
  function renderFinder() {
    var wrap = $('#finderChips'); if (!wrap) return;
    wrap.innerHTML = (window.SYMPTOMS || []).map(function (s) {
      return '<button type="button" class="chip" aria-pressed="false" data-symptom="' + s.id + '"><i class="fa-solid ' + esc(s.icon) + '"></i>' + esc(s.label) + '</button>';
    }).join('');
    wrap.addEventListener('click', function (e) {
      var chip = e.target.closest('.chip'); if (!chip) return;
      chip.setAttribute('aria-pressed', chip.getAttribute('aria-pressed') === 'true' ? 'false' : 'true');
      updateFinder();
    });
  }
  function updateFinder() {
    var out = $('#finderResult');
    var chosen = $$('.chip[aria-pressed="true"]').map(function (c) {
      var id = c.getAttribute('data-symptom');
      return (window.SYMPTOMS || []).filter(function (s) { return s.id === id; })[0];
    }).filter(Boolean);
    if (!chosen.length) { out.innerHTML = '<p class="finder-empty"><i class="fa-regular fa-hand-pointer"></i> Choose one or more symptoms above.</p>'; return; }

    var score = {}, order = [];
    chosen.forEach(function (s) {
      s.services.forEach(function (id, idx) {
        if (!(id in score)) { score[id] = 0; order.push(id); }
        score[id] += 3 - Math.min(idx, 2);
      });
    });
    order.sort(function (a, b) { return score[b] - score[a]; });
    var urgent = chosen.some(function (s) { return s.urgent; });
    var html = '';
    if (urgent) {
      html += '<div class="finder-urgent"><i class="fa-solid fa-triangle-exclamation"></i><div><strong>This may need prompt attention.</strong> Swelling, pus or a non-healing ulcer should be checked soon. ' +
        '<a href="' + esc(hasPhone ? telHref() : '#appointment') + '">Call us</a> or <a href="#appointment">book the earliest slot</a>.</div></div>';
    }
    html += '<div class="finder-list">' + order.slice(0, 4).map(function (id, i) {
      var s = serviceById(id); if (!s) return '';
      return '<div class="finder-item"><span class="svc-ic">' + svgIcon(id) + '</span><div>' +
        (i === 0 ? '<span class="match">Best match</span>' : '') +
        '<h3>' + esc(s.name) + '</h3><p>' + esc(s.short) + '</p>' +
        '<div class="links"><button type="button" data-service="' + id + '">Learn more</button><a href="#appointment" data-book="' + id + '">Book now →</a></div></div></div>';
    }).join('') + '</div>';
    out.innerHTML = html;
  }

  /* ---------- 7. Blog (articles open in a pop-up) ---------- */
  var BLOG = window.BLOG || [];
  function renderBlog() {
    var grid = $('#blogGrid'); if (!grid) return;
    grid.innerHTML = BLOG.map(function (b) {
      return '<article class="post reveal"><button type="button" class="post-link" data-article="' + b.id + '">' +
        '<span class="post-art tone-' + esc(b.tone) + '"><i class="fa-solid ' + esc(b.icon) + '"></i></span>' +
        '<span class="post-body"><span class="post-meta"><span class="post-tag">' + esc(b.tag) + '</span> · ' + b.minutes + ' min read</span>' +
        '<span class="post-title">' + esc(b.title) + '</span><span class="post-excerpt">' + esc(b.excerpt) + '</span>' +
        '<span class="post-more">Read article <i class="fa-solid fa-arrow-right"></i></span></span></button></article>';
    }).join('');
  }
  function openArticle(id) {
    var b = null; BLOG.forEach(function (x) { if (x.id === id) b = x; });
    if (!b || !modal) return;
    var svc = serviceById(b.service);
    $('#modalBody').innerHTML =
      '<div class="m-head article-head tone-' + esc(b.tone) + '"><span class="post-icon"><i class="fa-solid ' + esc(b.icon) + '"></i></span>' +
      '<div><span class="post-meta">' + esc(b.tag) + ' · ' + b.minutes + ' min read</span><h2 id="modalTitle">' + esc(b.title) + '</h2></div></div>' +
      '<div class="m-content article">' + (b.sections || []).map(function (sec) {
        return (sec.h ? '<h3>' + esc(sec.h) + '</h3>' : '') + (sec.p ? '<p>' + esc(sec.p) + '</p>' : '') +
          (sec.list ? '<ul>' + sec.list.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul>' : '');
      }).join('') +
      '<div class="m-foot"><a href="#appointment" class="btn btn-accent" data-close' + (svc ? ' data-book="' + svc.id + '"' : '') + '><i class="fa-regular fa-calendar-check"></i> Book a consultation</a>' +
      '<a class="btn btn-outline-wa" ' + (hasWa ? 'target="_blank" rel="noopener" ' : 'data-close ') + 'href="' + esc(waHref('Hello ' + C.name + ', I have a question about: ' + b.title)) + '"><i class="fa-brands fa-whatsapp"></i> Ask on WhatsApp</a>' +
      (svc ? '<button type="button" class="btn btn-ghost" data-service="' + svc.id + '">About ' + esc(svc.name) + '</button>' : '') + '</div>' +
      '<p class="m-disclaimer">General information only — please see a dentist for advice about your own teeth.</p></div>';
    lastFocus = document.activeElement;
    modal.hidden = false;
    document.body.classList.add('modal-open');
    $('.modal-dialog', modal).scrollTop = 0;
    $('.modal-close', modal).focus();
  }

  /* ---------- 8. Hero slider ---------- */
  function initSlider() {
    var hero = $('.hero'); if (!hero) return;
    var slides = $$('.slide', hero), dotsWrap = $('.slider-dots', hero), idx = 0, timer = null, paused = false;
    if (slides.length < 2) return;
    dotsWrap.innerHTML = slides.map(function (_, i) { return '<button role="tab" aria-label="Slide ' + (i + 1) + '" aria-selected="' + (i === 0) + '"></button>'; }).join('');
    var dots = $$('button', dotsWrap);
    function go(i) {
      idx = (i + slides.length) % slides.length;
      slides.forEach(function (s, k) {
        var on = k === idx;
        s.classList.toggle('is-active', on);
        s.setAttribute('aria-hidden', on ? 'false' : 'true');
        $$('a, button', s).forEach(function (f) { if (on) f.removeAttribute('tabindex'); else f.setAttribute('tabindex', '-1'); });
      });
      dots.forEach(function (d, k) { d.setAttribute('aria-selected', k === idx ? 'true' : 'false'); });
    }
    function play() { stop(); if (!reduceMotion && !paused) timer = setInterval(function () { go(idx + 1); }, 6500); }
    function stop() { if (timer) clearInterval(timer); timer = null; }
    dots.forEach(function (d, k) { d.addEventListener('click', function () { go(k); play(); }); });
    $('.slider-arrow.prev', hero).addEventListener('click', function () { go(idx - 1); play(); });
    $('.slider-arrow.next', hero).addEventListener('click', function () { go(idx + 1); play(); });
    hero.addEventListener('mouseenter', function () { paused = true; stop(); });
    hero.addEventListener('mouseleave', function () { paused = false; play(); });
    hero.addEventListener('focusin', function () { paused = true; stop(); });
    hero.addEventListener('focusout', function () { paused = false; play(); });
    document.addEventListener('visibilitychange', function () { if (document.hidden) stop(); else play(); });
    var x0 = null;
    hero.addEventListener('touchstart', function (e) { x0 = e.touches[0].clientX; }, { passive: true });
    hero.addEventListener('touchend', function (e) {
      if (x0 === null) return;
      var dx = e.changedTouches[0].clientX - x0; x0 = null;
      if (Math.abs(dx) > 50) { go(idx + (dx < 0 ? 1 : -1)); play(); }
    });
    go(0); play();
  }

  /* ---------- 9. Before / after comparison ---------- */
  function initBeforeAfter() {
    var cases = SC.beforeAfter || [], grid = $('#baGrid');
    if (grid) {
      grid.innerHTML = cases.map(function (c) {
        if (!c.before || !c.after) {
          var one = c.after || c.before;
          return '<figure class="ba ba-empty">' + (one
            ? '<div class="ba-stage"><img class="ba-single" src="' + esc(one) + '" alt="' + esc(c.title) + '" loading="lazy"><span class="ba-label ' + (c.after ? 'ba-l-after' : 'ba-l-before') + '">' + (c.after ? 'After' : 'Before') + '</span></div>'
            : '<div class="ba-stage ba-vacant"><i class="fa-regular fa-images"></i><span>Before &amp; after photos coming soon</span></div>') +
            '<figcaption><strong>' + esc(c.title) + '</strong></figcaption></figure>';
        }
        return '<figure class="ba" data-ba><div class="ba-stage">' +
          '<div class="ba-layer ba-after"><img src="' + esc(c.after) + '" alt="' + esc(c.title) + ' — after" loading="lazy"></div>' +
          '<div class="ba-layer ba-before"><img src="' + esc(c.before) + '" alt="' + esc(c.title) + ' — before" loading="lazy"></div>' +
          '<span class="ba-label ba-l-before">Before</span><span class="ba-label ba-l-after">After</span>' +
          '<div class="ba-handle" aria-hidden="true"><span><i class="fa-solid fa-left-right"></i></span></div>' +
          '<input type="range" min="0" max="100" value="50" class="ba-range" aria-label="Compare before and after: ' + esc(c.title) + '"></div>' +
          '<figcaption><strong>' + esc(c.title) + '</strong></figcaption></figure>';
      }).join('');
    }
    $$('[data-ba]').forEach(function (fig) {
      var stage = $('.ba-stage', fig), range = $('.ba-range', fig);
      function set() { stage.style.setProperty('--pos', range.value + '%'); }
      range.addEventListener('input', set); set();
    });

    var photos = SC.gallery || [], pg = $('#photoGallery');
    if (photos.length && pg) {
      pg.innerHTML = photos.map(function (p) {
        return '<figure><img src="' + esc(p.src) + '" alt="' + esc(p.caption || 'DENTZEN clinic') + '" loading="lazy">' + (p.caption ? '<figcaption>' + esc(p.caption) + '</figcaption>' : '') + '</figure>';
      }).join('');
      pg.hidden = false;
      var wrap = $('#clinicPhotos'); if (wrap) wrap.hidden = false;
    }
  }

  /* ---------- 10. Reviews ---------- */
  var SOURCES = {
    Google: { cls: 'src-google', icon: '<i class="fa-brands fa-google"></i>', link: 'googleReview' },
    Justdial: { cls: 'src-jd', icon: '<b>JD</b>', link: 'justdial' },
    Practo: { cls: 'src-practo', icon: '<b>p</b>', link: 'practo' }
  };
  function starsHtml(n) {
    var full = Math.round(n || 0), out = '';
    for (var i = 1; i <= 5; i++) out += '<i class="fa-' + (i <= full ? 'solid' : 'regular') + ' fa-star"></i>';
    return out;
  }
  function renderRatingCards() {
    var box = $('#ratingCards'); if (!box) return;
    var r = SC.ratings || {}, social = C.social || {}, cards = [];
    if (r.google) cards.push({ src: 'Google', score: r.google, sub: r.googleCount ? r.googleCount + ' Google reviews' : 'Google rating', btn: 'Read Google reviews' });
    if (r.justdial) cards.push({ src: 'Justdial', score: r.justdial, sub: r.justdialCount ? r.justdialCount + ' Justdial ratings' : 'Justdial rating', btn: 'Read Justdial reviews' });
    if (r.practo) cards.push({ src: 'Practo', pct: r.practo, sub: 'Patients recommend us on Practo', btn: 'View on Practo' });
    box.innerHTML = cards.map(function (c) {
      var m = SOURCES[c.src], url = social[m.link] || '#';
      return '<a class="rating-card ' + m.cls + '" href="' + esc(url) + '" target="_blank" rel="noopener">' +
        '<span class="rc-logo">' + m.icon + '</span><span class="rc-name">' + c.src + '</span>' +
        '<span class="rc-score">' + (c.pct ? c.pct + '%' : Number(c.score).toFixed(1)) + '</span>' +
        (c.pct ? '<span class="rc-stars rc-pct"><span style="width:' + Math.min(100, c.pct) + '%"></span></span>' : '<span class="rc-stars">' + starsHtml(c.score) + '</span>') +
        '<span class="rc-sub">' + esc(c.sub) + '</span><span class="rc-btn">' + c.btn + ' <i class="fa-solid fa-arrow-up-right-from-square"></i></span></a>';
    }).join('');
  }
  function initReviews() {
    renderRatingCards();
    var reviews = SC.reviews || [], slider = $('#reviewsSlider'), track = $('#reviewsTrack');
    if (!reviews.length || !slider) return;
    track.innerHTML = reviews.map(function (r) {
      var stars = Math.max(1, Math.min(5, r.rating || 5)), m = SOURCES[r.source] || null;
      return '<article class="review"><div class="review-top"><span class="stars" aria-label="' + stars + ' out of 5 stars">' + starsHtml(stars) + '</span>' +
        (m ? '<span class="rv-src ' + m.cls + '" title="' + esc(r.source) + ' review">' + m.icon + '</span>' : '') + '</div>' +
        '<p>“' + esc(r.text) + '”</p><div class="who"><span class="avatar">' + esc((r.name || '?').charAt(0).toUpperCase()) + '</span>' +
        '<div><strong>' + esc(r.name) + '</strong><span>' + esc((r.source ? r.source + ' review' : 'Patient') + (r.date ? ' · ' + r.date : '')) + '</span></div></div></article>';
    }).join('');
    slider.hidden = false;
    $$('[data-rev]', slider).forEach(function (b) {
      b.addEventListener('click', function () {
        var card = $('.review', track), w = card ? card.offsetWidth + 24 : 300;
        track.scrollBy({ left: b.getAttribute('data-rev') === 'next' ? w : -w });
      });
    });
  }

  /* ---------- 11. Booking form ---------- */
  function initBooking() {
    var form = $('#bookingForm'); if (!form) return;
    var dateIn = $('#fDate'), slotsBox = $('#slots');
    var today = istNow(), max = new Date(today.getTime() + 60 * 864e5);
    dateIn.min = isoDate(today); dateIn.max = isoDate(max);

    function weekdayOf(iso) { var p = iso.split('-'); return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2])).getUTCDay(); }
    function renderSlots() {
      var iso = dateIn.value;
      if (!iso) { slotsBox.innerHTML = '<p class="slots-hint">Select a date to see available times.</p>'; return; }
      var sessions = sessionsFor(weekdayOf(iso)), step = C.slotMinutes || 30;
      if (!sessions.length) { slotsBox.innerHTML = '<p class="slots-hint">The clinic is closed on this day. Please choose another date.</p>'; return; }
      var apptNote = byAppt(weekdayOf(iso)) ? '<p class="slots-hint slots-note"><i class="fa-solid fa-circle-info"></i> Thursday is by appointment only — we will confirm your slot by phone or WhatsApp.</p>' : '';
      var t = istNow(), isToday = iso === isoDate(t), nowMin = t.getHours() * 60 + t.getMinutes() + 30;
      var html = '', any = false;
      sessions.forEach(function (r) {
        var a = toMin(r[0]), b = toMin(r[1]);
        html += '<span class="slot-group">' + (a < 720 ? 'Morning' : a < 1020 ? 'Afternoon' : 'Evening') + '</span>';
        for (var m = a; m + step <= b; m += step) {
          var dis = isToday && m < nowMin;
          if (!dis) any = true;
          html += '<label class="slot"><input type="radio" name="slot" value="' + fmtTime(m) + '"' + (dis ? ' disabled' : '') + '><span>' + fmtTime(m) + '</span></label>';
        }
      });
      slotsBox.innerHTML = any ? apptNote + html : '<p class="slots-hint">No more slots today. Please choose another date.</p>';
    }
    dateIn.addEventListener('change', function () { renderSlots(); clearErr(dateIn); $('#slotErr').textContent = ''; });
    slotsBox.addEventListener('change', function () { $('#slotErr').textContent = ''; });

    function setErr(input, msg) { var f = input.closest('.field'); f.classList.add('invalid'); $('.err', f).textContent = msg; }
    function clearErr(input) { var f = input.closest('.field'); f.classList.remove('invalid'); $('.err', f).textContent = ''; }
    $$('input, select, textarea', form).forEach(function (i) { i.addEventListener('input', function () { if (i.closest('.field')) clearErr(i); }); });

    function niceDate(iso) {
      var p = iso.split('-'), d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2]));
      return d.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
    }

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      var name = $('#fName'), phone = $('#fPhone'), slot = $('input[name="slot"]:checked', form), ok = true;
      var mob = digits(phone.value).replace(/^(91|0)(?=\d{10}$)/, '');
      if (name.value.trim().length < 2) { setErr(name, 'Please enter your name.'); ok = false; }
      if (!/^[6-9]\d{9}$/.test(mob)) { setErr(phone, 'Please enter a valid 10-digit mobile number.'); ok = false; }
      if (!dateIn.value || dateIn.value < dateIn.min) { setErr(dateIn, 'Please choose a date from today onwards.'); ok = false; }
      if (dateIn.value && !slot) { $('#slotErr').textContent = 'Please pick a time slot.'; ok = false; }
      if (!$('#fConsent').checked) { $('#consentErr').textContent = 'Please tick the box so we can contact you.'; ok = false; } else { $('#consentErr').textContent = ''; }
      if (!ok) { var bad = $('.invalid input, .invalid select', form); if (bad) bad.focus(); return; }

      var data = {
        name: name.value.trim(), phone: '+91 ' + mob, service: $('#fService').value,
        date: niceDate(dateIn.value), time: slot.value, message: $('#fMsg').value.trim()
      };
      var summary = 'Name: ' + data.name + '\nMobile: ' + data.phone + '\nTreatment: ' + data.service +
        '\nPreferred date: ' + data.date + '\nPreferred time: ' + data.time + (data.message ? '\nNote: ' + data.message : '');
      var waText = 'Hello ' + C.name + ', I would like to book an appointment.\n\n' + summary;

      if (C.formEndpoint) {
        var btn = $('button[type="submit"]', form); btn.disabled = true; $('#submitText').textContent = 'Sending…';
        fetch(C.formEndpoint, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' }, body: JSON.stringify(data) })
          .then(function (r) { if (!r.ok) throw new Error(r.status); showDone(data, summary, waText, 'sent'); })
          .catch(function () { showDone(data, summary, waText, hasWa ? 'whatsapp' : 'manual'); if (hasWa) window.open(waHref(waText), '_blank', 'noopener'); })
          .then(function () { btn.disabled = false; $('#submitText').textContent = 'Send Booking Request'; });
        return;
      }
      if (hasWa) { window.open(waHref(waText), '_blank', 'noopener'); showDone(data, summary, waText, 'whatsapp'); }
      else showDone(data, summary, waText, 'manual');
    });

    function showDone(data, summary, waText, mode) {
      var msgs = {
        sent: 'Your request has been sent. We will call or WhatsApp you shortly to confirm your appointment.',
        whatsapp: 'WhatsApp has opened with your booking details — just press send. We will confirm your appointment shortly.',
        manual: 'Please share these details with the clinic by phone, or book on Practo, and we will confirm your appointment.'
      };
      $('#doneName').textContent = data.name.split(' ')[0];
      $('#doneMsg').textContent = msgs[mode];
      $('#doneSummary').textContent = summary;
      var actions = '';
      if (hasWa) actions += '<a class="btn btn-wa" target="_blank" rel="noopener" href="' + esc(waHref(waText)) + '"><i class="fa-brands fa-whatsapp"></i> ' + (mode === 'whatsapp' ? 'Open WhatsApp again' : 'Send on WhatsApp') + '</a>';
      if (hasPhone) actions += '<a class="btn btn-navy" href="' + telHref() + '"><i class="fa-solid fa-phone"></i> Call clinic</a>';
      if (mode === 'manual' && C.social && C.social.practo) actions += '<a class="btn btn-navy" target="_blank" rel="noopener" href="' + esc(C.social.practo) + '"><i class="fa-solid fa-arrow-up-right-from-square"></i> Book on Practo</a>';
      actions += '<button type="button" class="btn btn-ghost" id="copySummary"><i class="fa-regular fa-copy"></i> Copy details</button>';
      $('#doneActions').innerHTML = actions;
      $('#copySummary').addEventListener('click', function () {
        var b = this;
        if (navigator.clipboard) navigator.clipboard.writeText(waText).then(function () { b.innerHTML = '<i class="fa-solid fa-check"></i> Copied'; });
      });
      form.hidden = true; $('#bookingDone').hidden = false;
      $('#bookingDone').scrollIntoView({ behavior: reduceMotion ? 'auto' : 'smooth', block: 'center' });
    }
    $('#bookAnother').addEventListener('click', function () {
      form.reset(); renderSlots(); form.hidden = false; $('#bookingDone').hidden = true; $('#fName').focus();
    });
  }

  /* ---------- 12. Header, navigation & global clicks ---------- */
  function initNav() {
    var header = $('#siteHeader'), toggle = $('#navToggle'), nav = $('#mainNav'), toTop = $('#toTop'), overlay = null;
    function onScroll() {
      var y = window.scrollY;
      header.classList.toggle('scrolled', y > 10);
      if (toTop) toTop.classList.toggle('show', y > 700);
    }
    window.addEventListener('scroll', onScroll, { passive: true }); onScroll();
    if (toTop) toTop.addEventListener('click', function () { window.scrollTo({ top: 0, behavior: reduceMotion ? 'auto' : 'smooth' }); });

    function setMenu(open) {
      nav.classList.toggle('open', open);
      toggle.setAttribute('aria-expanded', open);
      toggle.setAttribute('aria-label', open ? 'Close menu' : 'Open menu');
      if (open && !overlay) { overlay = document.createElement('div'); overlay.className = 'nav-overlay'; overlay.addEventListener('click', function () { setMenu(false); }); document.body.appendChild(overlay); }
      if (!open && overlay) { overlay.remove(); overlay = null; }
    }
    toggle.addEventListener('click', function () { setMenu(!nav.classList.contains('open')); });

    var ddToggle = $('.dropdown-toggle', nav);
    ddToggle.addEventListener('click', function (e) {
      if (window.matchMedia('(max-width: 1200px)').matches) {
        e.preventDefault();
        var li = ddToggle.parentNode, open = !li.classList.contains('open');
        li.classList.toggle('open', open); ddToggle.setAttribute('aria-expanded', open);
      }
    });
    nav.addEventListener('click', function (e) {
      var a = e.target.closest('a');
      if (a && !a.classList.contains('dropdown-toggle')) setMenu(false);
    });

    /* Highlight the menu item for the section in view */
    if ('IntersectionObserver' in window) {
      var links = $$('.main-nav > ul > li > a');
      var spy = new IntersectionObserver(function (entries) {
        entries.forEach(function (en) {
          if (!en.isIntersecting) return;
          links.forEach(function (l) { l.classList.toggle('active', l.getAttribute('href') === '#' + en.target.id); });
        });
      }, { rootMargin: '-45% 0px -50% 0px' });
      $$('main section[id]').forEach(function (s) { spy.observe(s); });
    }

    /* Delegated clicks: open treatment details, pre-select treatment when booking */
    document.addEventListener('click', function (e) {
      var art = e.target.closest('[data-article]');
      if (art) { e.preventDefault(); openArticle(art.getAttribute('data-article')); return; }
      var svc = e.target.closest('[data-service]');
      if (svc) { e.preventDefault(); openService(svc.getAttribute('data-service')); return; }
      var book = e.target.closest('[data-book]');
      if (book) {
        var s = serviceById(book.getAttribute('data-book')), sel = $('#fService');
        if (s && sel) sel.value = s.id === 'checkup' ? 'General consultation' : s.name;
      }
      if (e.target.closest('[data-close]')) closeModal();
    });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { closeModal(); if (nav.classList.contains('open')) { setMenu(false); toggle.focus(); } }
      if (e.key === 'Tab' && modal && !modal.hidden) {
        var f = $$('a[href], button:not([disabled])', modal), first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });
  }

  /* ---------- 13. Reveal on scroll ---------- */
  function initReveal() {
    var items = $$('.reveal');
    if (reduceMotion || !('IntersectionObserver' in window)) { items.forEach(function (i) { i.classList.add('in'); }); return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, { threshold: .12, rootMargin: '0px 0px -40px 0px' });
    items.forEach(function (i) { io.observe(i); });
  }

  /* ---------- 14. Structured data for Google (Dentist + FAQ) ---------- */
  function injectSchema() {
    var dayMap = { 0: 'Sunday', 1: 'Monday', 2: 'Tuesday', 3: 'Wednesday', 4: 'Thursday', 5: 'Friday', 6: 'Saturday' };
    var hours = [];
    Object.keys(C.hours || {}).forEach(function (d) {
      C.hours[d].forEach(function (r) { hours.push({ '@type': 'OpeningHoursSpecification', dayOfWeek: dayMap[d], opens: r[0], closes: r[1] }); });
    });
    var dentist = {
      '@context': 'https://schema.org', '@type': 'Dentist', name: C.name,
      description: 'Dental hospital in Kankarbagh, Patna offering check-ups, root canal treatment, implants, braces, aligners, whitening and kids dentistry.',
      address: { '@type': 'PostalAddress', streetAddress: addr.line1 + (addr.landmark ? ' (' + addr.landmark + ')' : ''), addressLocality: [addr.area, addr.city].filter(Boolean).join(', '), addressRegion: addr.state, postalCode: addr.pin || undefined, addressCountry: 'IN' },
      openingHoursSpecification: hours,
      employee: { '@type': 'Person', name: C.doctor && C.doctor.name, jobTitle: C.doctor && C.doctor.title, hasCredential: C.doctor && C.doctor.degree },
      sameAs: Object.keys(C.social || {}).map(function (k) { return C.social[k]; }).filter(function (u) { return /^https?:/.test(u); })
    };
    if (hasPhone) dentist.telephone = '+' + phoneIntl;
    if (C.email) dentist.email = C.email;
    if (C.siteUrl) dentist.url = C.siteUrl;
    if (C.consultationFee) dentist.priceRange = '₹₹';
    var faq = {
      '@context': 'https://schema.org', '@type': 'FAQPage',
      mainEntity: $$('#faqList details').map(function (d) {
        return { '@type': 'Question', name: $('summary', d).textContent.trim(), acceptedAnswer: { '@type': 'Answer', text: $('.faq-body', d).textContent.trim() } };
      })
    };
    [dentist, faq].forEach(function (obj) {
      var s = document.createElement('script'); s.type = 'application/ld+json'; s.textContent = JSON.stringify(obj); document.head.appendChild(s);
    });
  }

  /* ---------- go ---------- */
  bindConfig();
  renderStatus(); renderHours();
  setInterval(renderStatus, 60000);
  renderStats();
  renderServices();
  renderFinder();
  renderBlog();
  initSlider();
  initBeforeAfter();
  initReviews();
  initBooking();
  initNav();
  initReveal();
  injectSchema();
})();
