/* ------------------------------------------------------------------
   Practice configuration. Edit this block only.
   ------------------------------------------------------------------ */
var CONFIG = {
  name: "BrightSmile Dental",
  tagline: "Gentle, modern dentistry for the whole family",
  phone: "+911234567890",            // E.164 format, used for call and SMS
  whatsapp: "911234567890",          // digits only, country code first
  bookingSmsTo: "+911234567890",     // number that receives booking texts
  feedbackEmail: "manager@example.com",
  googleReviewUrl: "https://g.page/r/REPLACE_WITH_YOUR_PLACE_ID/review",
  mapsUrl: "https://maps.google.com/?q=BrightSmile+Dental+Patna",
  address: "Ground Floor, Alpana Market, Boring Road, Patna 800013",
  rating: "4.9",
  reviewCount: "312",
  offer: {
    title: "Exam, X-rays & cleaning",
    text: "Mention this page when you book. Valid for first-time patients only."
  },
  services: [
    "Root canal (single sitting)", "Dental implants", "Braces & aligners",
    "Teeth whitening", "Scaling & cleaning", "Crowns & bridges",
    "Kids dentistry", "Wisdom tooth removal", "Emergency care"
  ],
  // 0 = Sunday … 6 = Saturday. Use null for closed. 24-hour clock.
  hours: [
    { open: "10:00", close: "13:00" },
    { open: "09:00", close: "20:00" },
    { open: "09:00", close: "20:00" },
    { open: "09:00", close: "20:00" },
    { open: "09:00", close: "20:00" },
    { open: "09:00", close: "20:00" },
    { open: "09:00", close: "20:00" }
  ]
};

/* ------------------------------------------------------------------ */
(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };
  var DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

  /* Header */
  document.title = CONFIG.name;
  $("practice-name").textContent = CONFIG.name;
  $("practice-tagline").textContent = CONFIG.tagline;
  $("rating-text").textContent = CONFIG.rating + " · " + CONFIG.reviewCount + " Google reviews";
  $("gate-url").textContent = location.href;
  $("footer-text").textContent = "© " + new Date().getFullYear() + " " + CONFIG.name;

  /* Offer */
  $("offer-title").textContent = CONFIG.offer.title;
  $("offer-text").textContent = CONFIG.offer.text;

  /* Links */
  var telHref = "tel:" + CONFIG.phone;
  var waHref = "https://wa.me/" + CONFIG.whatsapp + "?text=" +
    encodeURIComponent("Hi, I'd like to book an appointment at " + CONFIG.name + ".");
  $("call-btn").href = telHref;
  $("tab-call").href = telHref;
  $("whatsapp-btn").href = waHref;
  $("directions-btn").href = CONFIG.mapsUrl;
  $("tab-map").href = CONFIG.mapsUrl;
  $("review-btn").href = CONFIG.googleReviewUrl;
  $("address").textContent = CONFIG.address;

  /* Services: chips and select options */
  var list = $("services-list");
  var select = $("service-select");
  CONFIG.services.forEach(function (s) {
    var li = document.createElement("li");
    li.textContent = s;
    list.appendChild(li);
    var opt = document.createElement("option");
    opt.value = s;
    opt.textContent = s;
    select.appendChild(opt);
  });
  var other = document.createElement("option");
  other.value = "Not sure / other";
  other.textContent = "Not sure / other";
  select.appendChild(other);

  /* Hours table and open/closed status */
  var now = new Date();
  var today = now.getDay();
  var table = $("hours-table");
  CONFIG.hours.forEach(function (h, i) {
    var tr = document.createElement("tr");
    if (i === today) tr.className = "today";
    var d = document.createElement("td");
    d.textContent = DAYS[i];
    var t = document.createElement("td");
    t.textContent = h ? fmt(h.open) + " – " + fmt(h.close) : "Closed";
    tr.appendChild(d);
    tr.appendChild(t);
    table.appendChild(tr);
  });

  var status = $("open-status");
  var h = CONFIG.hours[today];
  if (h && isOpen(h, now)) {
    status.textContent = "Open now · closes " + fmt(h.close);
    status.className = "open-status open";
  } else {
    status.textContent = "Closed now" + (h ? " · opens " + fmt(h.open) : "");
    status.className = "open-status closed";
  }

  function fmt(hhmm) {
    var p = hhmm.split(":");
    var hr = parseInt(p[0], 10);
    var suffix = hr >= 12 ? "pm" : "am";
    hr = hr % 12 || 12;
    return hr + (p[1] !== "00" ? ":" + p[1] : "") + " " + suffix;
  }
  function isOpen(hrs, d) {
    var mins = d.getHours() * 60 + d.getMinutes();
    return mins >= toMin(hrs.open) && mins < toMin(hrs.close);
  }
  function toMin(hhmm) {
    var p = hhmm.split(":");
    return parseInt(p[0], 10) * 60 + parseInt(p[1], 10);
  }

  /* Booking form: builds an SMS the patient sends themselves */
  var form = $("book-form");
  var err = $("form-error");
  var success = $("book-success");
  var lastSmsHref = "";

  var dateInput = form.elements.date;
  dateInput.min = now.toISOString().slice(0, 10);

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    err.hidden = true;

    var f = form.elements;
    var name = f.name.value.trim();
    var phone = f.phone.value.trim();
    var service = f.service.value;
    var date = f.date.value;
    var time = f.time.value;
    var notes = f.notes.value.trim();
    var isNew = f.newPatient.checked;

    if (!name || !phone || !service || !date) {
      err.textContent = "Please fill in your name, number, service and preferred date.";
      err.hidden = false;
      return;
    }
    if (phone.replace(/\D/g, "").length < 10) {
      err.textContent = "That mobile number looks too short.";
      err.hidden = false;
      return;
    }

    var body =
      "Appointment request\n" +
      "Name: " + name + "\n" +
      "Phone: " + phone + "\n" +
      "Service: " + service + "\n" +
      "Preferred: " + date + ", " + time + "\n" +
      (isNew ? "New patient: yes\n" : "") +
      (notes ? "Notes: " + notes + "\n" : "");

    lastSmsHref = smsHref(CONFIG.bookingSmsTo, body);
    $("resend-btn").href = lastSmsHref;

    try { localStorage.setItem("lastBooking", JSON.stringify({ name: name, phone: phone })); } catch (_) {}

    form.hidden = true;
    success.hidden = false;
    location.href = lastSmsHref;
  });

  $("reset-btn").addEventListener("click", function () {
    form.reset();
    success.hidden = true;
    form.hidden = false;
    form.elements.name.focus();
  });

  /* Prefill from a previous visit */
  try {
    var prev = JSON.parse(localStorage.getItem("lastBooking") || "null");
    if (prev) {
      form.elements.name.value = prev.name || "";
      form.elements.phone.value = prev.phone || "";
    }
  } catch (_) {}

  /* iOS uses "&body=", Android uses "?body=" */
  function smsHref(to, body) {
    var sep = /iPhone|iPad|iPod/i.test(navigator.userAgent) ? "&" : "?";
    return "sms:" + to + sep + "body=" + encodeURIComponent(body);
  }

  /* Private feedback */
  var fbToggle = $("feedback-toggle");
  var fbForm = $("feedback-form");
  fbToggle.addEventListener("click", function () {
    fbForm.hidden = !fbForm.hidden;
    if (!fbForm.hidden) fbForm.elements.feedback.focus();
  });
  fbForm.addEventListener("submit", function (e) {
    e.preventDefault();
    var text = fbForm.elements.feedback.value.trim();
    if (!text) return;
    location.href = "mailto:" + CONFIG.feedbackEmail +
      "?subject=" + encodeURIComponent("Patient feedback for " + CONFIG.name) +
      "&body=" + encodeURIComponent(text);
  });
})();
