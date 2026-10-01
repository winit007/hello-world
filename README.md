# DENTZEN Dental Hospital — Website

Website for **DENTZEN Dental Hospital**, Bhootnath Road, Kankarbagh, Patna — **Dr. W. Wagisha, BDS**.

It is a static site (HTML, CSS and JavaScript only), so it can be hosted free on GitHub Pages or any web host. Open `index.html` in a browser to preview it.

## What's on the site

- Sticky header with **Book Appointment**, WhatsApp and call buttons, and a top bar showing a live **"Open now / Closed"** status in Indian time
- Hero slider (general care, implants, braces & aligners)
- Counter band, About section, and **12 treatments**, each with a detailed pop-up (what it is, signs you need it, steps, duration, aftercare)
- **Treatment Finder**: patients tap their symptoms and get suggested treatments, with an urgent-care warning for swelling or non-healing ulcers
- Why choose us, a dental-emergency banner, modern care, the doctor's profile, and a "your visit, step by step" guide
- **Smile Gallery** with a drag-to-compare before/after slider
- Reviews section, plus buttons asking patients to review you on Google and Practo
- **Online booking form** with a date picker and time slots that follow your real clinic hours, sent to the clinic's WhatsApp
- Dental tips, FAQ, contact cards with an hours table (today highlighted), Google Map and directions
- Mobile quick-action bar (Call · WhatsApp · Book · Directions), floating WhatsApp button and back-to-top button
- Google-friendly structured data (Dentist + FAQ) for better search listings

## Editing clinic details — `assets/js/config.js`

Phone, WhatsApp, email, address, timings, consultation fee, social links, counters, reviews, before/after photos and the clinic gallery are all set in **one file**: `assets/js/config.js`. Every button and section on the site updates from it.

## Changing colours — `assets/css/style.css`

The whole colour scheme is defined at the top of `assets/css/style.css` (the `:root` block). Change a value there and it updates everywhere on the site.

## Photos

- `images/dr-wagisha.jpg` and `images/clinic/*.jpg`: the clinic's own photos (from its Practo listing). Replace them with higher-quality originals for a sharper look.
- `images/treatments/<treatment>.jpg`, `images/hero/*.jpg` and `images/tech/*.jpg`: free photos from [Unsplash](https://unsplash.com/license), which may be used commercially without credit. To change a picture, replace the file and keep its name.

## Still to do

1. **Google reviews:** Google blocks automated copying, so paste your reviews into `reviews` in `assets/js/config.js` (format shown there). The reviews carousel appears automatically.
2. **More counters** (total patients, implants, extractions, braces…): add them to `stats` in `assets/js/config.js`.
3. **Confirm:** the email (`dentzendentalcare@gmail.com`), the PIN code (800026) and the landmark.
4. **Before/after photos** of real patients (with their consent), for the Smile Gallery.
