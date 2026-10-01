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

## Website editor (admin.html): upload photos and add reviews

Open **`/admin.html`** on your website (for example `https://<your-site>/admin.html`). From there you can:

- upload the About photo, Dr. Wagisha's photo, clinic gallery photos and treatment pictures
- add before/after cases (each with a "before" and an "after" photo)
- add Google, Justdial and Practo reviews
- update ratings and the counter numbers

Press **Publish changes** and the edits are saved to this GitHub repository. GitHub Pages then updates the live site in about 1–2 minutes.

**One-time setup:** the editor needs a GitHub *fine-grained access token* for this repository, with **Contents: Read and write** permission. The steps are shown on the editor page under "How do I get an access token?". The token is stored only in your own browser.

Everything the editor changes lives in `assets/js/content.js`. Uploaded photos go into `images/uploads/`.

## Editing clinic details: `assets/js/config.js`

Phone, WhatsApp, email, address, timings, consultation fee and social links are in `assets/js/config.js`.

## Changing colours: `assets/css/style.css`

The whole colour scheme is defined at the top of `assets/css/style.css` (the `:root` block).

## Photos

- `images/dr-wagisha.jpg` and `images/clinic/*.jpg`: the clinic's own photos (from its Practo listing).
- `images/treatments/kids.svg` and `images/treatments/cleaning.svg`: drawn graphics.
- Other pictures in `images/treatments/`, `images/hero/` and `images/tech/`: free photos from [Unsplash](https://unsplash.com/license), which may be used commercially without credit.

## Still to do

1. **Total OPD** counter: add the number in the editor (Ratings & Counters). It stays hidden until then.
2. **About photo** and **before/after photos**: upload them in the editor.
3. **Reviews**: paste your Google and Justdial reviews in the editor.
4. **Confirm** the email (`dentzendentalcare@gmail.com`), the PIN code (800026) and the landmark.
