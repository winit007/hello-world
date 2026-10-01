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
    registration: 'Reg. No. 8555/A, Bihar State Dental Council',
    photo: 'images/dr-wagisha.jpg'     // replace this file with a newer photo any time
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
    0: [['10:00', '13:00']],
    1: [['10:00', '13:00'], ['15:00', '20:00']],
    2: [['10:00', '13:00'], ['15:00', '20:00']],
    3: [['10:00', '13:00'], ['15:00', '20:00']],
    4: [['10:00', '13:00'], ['15:00', '20:00']],
    5: [['10:00', '13:00'], ['15:00', '20:00']],
    6: [['10:00', '13:00'], ['15:00', '20:00']]
  },
  slotMinutes: 30,                     // length of each appointment slot in the booking form

  social: {
    instagram: 'https://www.instagram.com/dentzen_dental_care/',
    facebook: 'https://www.facebook.com/dentzendentalcare/',
    youtube: '',
    practo: 'https://www.practo.com/patna/doctor/w-wagisha-dentist',
    googleReview: 'https://share.google/0Wwl9Gnd1h5eXWbHB'   // your Google Business profile
  },

  /* Optional: an online form service (e.g. https://formspree.io/f/xxxx).
     When empty, appointment requests are sent to the clinic WhatsApp. */
  formEndpoint: '',

  /* Numbers shown in the green counter band (3 per row on desktop).
     'years' is calculated automatically from doctor.graduationYear.
     Add more of your real numbers in the same format, e.g.
       { icon: 'fa-face-smile',  value: 15000, suffix: '+', label: 'Happy Patients' },
       { icon: 'fa-screwdriver', value: 300,   suffix: '+', label: 'Dental Implants' }, */
  stats: [
    { icon: 'fa-tooth',              value: 8000,    suffix: '+',     label: 'Root Canal Treatments' },
    { icon: 'fa-user-doctor',        value: 'years', suffix: '+',     label: 'Years of Experience' },
    { icon: 'fa-star',               value: 4.9,     suffix: '★',     label: 'Justdial Rating (76 reviews)' },
    { icon: 'fa-teeth-open',         value: 25,      suffix: '+',     label: 'Dental Treatments' },
    { icon: 'fa-calendar-check',     value: 7,       suffix: ' Days', label: 'Open Every Week' },
    { icon: 'fa-indian-rupee-sign',  value: 250,     prefix: '₹',     label: 'Consultation Fee' }
  ],

  /* Real patient reviews, copied from Google (the carousel appears once you add some):
       { name: 'Patient name', text: 'Review text…', rating: 5, source: 'Google' }, */
  reviews: [],

  /* Real before/after photos. Put the files in the images folder, e.g.
       { title: 'Teeth whitening', before: 'images/case1-before.jpg', after: 'images/case1-after.jpg' }, */
  beforeAfter: [],

  /* Clinic photos shown under "Our Clinic" in the Smile Gallery section. */
  gallery: [
    { src: 'images/clinic/entrance.jpg', caption: 'Clinic entrance' },
    { src: 'images/clinic/reception.jpg', caption: 'Reception & waiting area' },
    { src: 'images/clinic/treatment-room.jpg', caption: 'Treatment room' }
  ],

  siteUrl: ''                          // e.g. 'https://www.dentzendental.com' once you have a domain
};
