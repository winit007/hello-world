/* =====================================================================
   DENTZEN DENTAL HOSPITAL — WEBSITE SETTINGS
   ---------------------------------------------------------------------
   This is the ONLY file you need to edit to update clinic details.
   Every phone button, WhatsApp link, timing table, map and the booking
   form on the website reads its information from here.
   ===================================================================== */
window.CLINIC = {
  name: 'DENTZEN Dental Hospital',
  shortName: 'DENTZEN',
  tagline: 'Calm, caring dentistry for the whole family',

  doctor: {
    name: 'Dr. W. Wagisha',
    shortName: 'Dr. Wagisha',
    degree: 'BDS',
    title: 'Aesthetic, Cosmetic & Dental Surgeon',
    college: 'Buddha Dental College',
    graduationYear: 2019,              // "years of experience" is calculated from this
    registration: 'Reg. No. 8555/A, Bihar State Dental Council'
    // the doctor's photo is set from the admin page (assets/js/content.js)
  },

  /* Phone & WhatsApp — include the country code. */
  phone: '+91 87895 29485',
  whatsapp: '',                        // leave '' to use the phone number above
  email: 'dentzendentalcare@gmail.com',

  address: {
    line1: 'Bhootnath Road',
    area: 'Kankarbagh',
    city: 'Patna',
    state: 'Bihar',
    pin: '800026',
    landmark: "Ground floor, right of Domino's, opposite Bhootnath Mandir"
  },
  mapQuery: 'Dentzen Dental Hospital, Bhootnath Road, Kankarbagh, Patna, Bihar',

  consultationFee: 250,                // ₹ — set to 0 to hide

  /* Opening hours, 24-hour format. 0 = Sunday, 1 = Monday … 6 = Saturday.
     Each day can have several sessions, e.g. a lunch break. */
  hours: {
    0: [['10:00', '20:00']],
    1: [['10:00', '20:00']],
    2: [['10:00', '20:00']],
    3: [['10:00', '20:00']],
    4: [['10:00', '20:00']],
    5: [['10:00', '20:00']],
    6: [['10:00', '20:00']]
  },
  appointmentOnly: [4],                // days seen by prior appointment only (4 = Thursday)
  slotMinutes: 30,                     // length of each appointment slot in the booking form

  social: {
    instagram: 'https://www.instagram.com/dentzen_dental_care/',
    facebook: 'https://www.facebook.com/dentzendentalcare/',
    youtube: '',
    practo: 'https://www.practo.com/patna/doctor/w-wagisha-dentist',
    justdial: 'https://www.justdial.com/Patna/Dentzen-Dental-Care-Opposite-Bihar-Rajya-Awas-Board-Bhootnath-Mandirnear-Domi-Bhootnath-Road-Kankarbagh/0612PX612-X612-220309183103-R6P5_BZDET',
    googleReview: 'https://share.google/0Wwl9Gnd1h5eXWbHB'   // your Google Business profile
  },

  /* Optional: an online form service (e.g. https://formspree.io/f/xxxx).
     When empty, appointment requests are sent to the clinic WhatsApp. */
  formEndpoint: '',

  /* Photos, reviews, ratings and the counter numbers are in assets/js/content.js
     and can be edited from the admin page (admin.html). */

  /* Patient Safety & Infection Control section.
     It stays HIDDEN on the website until you set verified: true, after checking
     that every step on the page matches what the clinic actually does. */
  infectionControl: {
    verified: false,
    chemical: {                        // fill in to show the product on the page; leave '' to hide
      product: '',                     // e.g. 'Cidex OPA (ortho-phthalaldehyde)'
      concentration: '',               // e.g. '0.55%'
      contactTime: '',                 // e.g. '12 minutes'
      usedFor: ''                      // e.g. 'heat-sensitive items only'
    }
  },

  siteUrl: ''                          // e.g. 'https://www.dentzendental.com' once you have a domain
};
