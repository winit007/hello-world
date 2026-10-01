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
    photo: ''                          // e.g. 'images/dr-wagisha.jpg' — leave '' to show the placeholder
  },

  /* >>> TODO: add your real numbers. Include the country code. <<<
     Until a 10-digit number is entered, call/WhatsApp buttons scroll
     to the booking form instead of dialling. */
  phone: '+91 XXXXX XXXXX',
  whatsapp: '',                        // leave '' to use the phone number above
  email: '',                           // e.g. 'dentzendental@gmail.com' — hidden when empty

  address: {
    line1: 'Bhootnath Road',
    area: 'Kankarbagh',
    city: 'Patna',
    state: 'Bihar',
    pin: '',                           // TODO: add PIN code
    landmark: "Ground floor, right of Domino's, opposite Bhootnath Mandir"
  },
  mapQuery: 'Dentzen Dental Care, Bhootnath Road, Kankarbagh, Patna, Bihar',

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
    facebook: '',
    youtube: '',
    practo: 'https://www.practo.com/patna/doctor/w-wagisha-dentist',
    googleReview: ''                   // your Google "write a review" link, if you have one
  },

  /* Optional: an online form service (e.g. https://formspree.io/f/xxxx).
     When empty, appointment requests are sent to the clinic WhatsApp. */
  formEndpoint: '',

  /* Numbers shown in the green counter band.
     'years' is calculated automatically from doctor.graduationYear.
     Add your real patient / treatment numbers when you have them, e.g.
       { icon: 'fa-face-smile', value: 5000, suffix: '+', label: 'Happy Patients' },
       { icon: 'fa-tooth',      value: 1200, suffix: '+', label: 'Root Canals' }, */
  stats: [
    { icon: 'fa-user-doctor',        value: 'years', suffix: '+',     label: 'Years of Experience' },
    { icon: 'fa-tooth',              value: 25,      suffix: '+',     label: 'Dental Treatments' },
    { icon: 'fa-calendar-check',     value: 7,       suffix: ' Days', label: 'Open Every Week' },
    { icon: 'fa-indian-rupee-sign',  value: 250,     prefix: '₹',     label: 'Consultation Fee' },
    { icon: 'fa-graduation-cap',     value: 2019,    plain: true,     label: 'Practising Since' }
  ],

  /* Real patient reviews (copy them from Google / Practo with permission).
     The reviews carousel appears automatically once you add some:
       { name: 'Patient name', text: 'Review text…', rating: 5, source: 'Google' }, */
  reviews: [],

  /* Real before/after photos. Put the files in the images folder, e.g.
       { title: 'Teeth whitening', before: 'images/case1-before.jpg', after: 'images/case1-after.jpg' }, */
  beforeAfter: [],

  /* Clinic photos for the gallery (section appears once you add some):
       { src: 'images/reception.jpg', caption: 'Reception' }, */
  gallery: [],

  siteUrl: ''                          // e.g. 'https://www.dentzendental.com' once you have a domain
};
