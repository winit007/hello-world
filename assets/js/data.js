/* =====================================================================
   DENTZEN — TREATMENTS, TREATMENT FINDER & DENTAL TIPS CONTENT
   ===================================================================== */

/* Line icons for each treatment (48×48, drawn with the current text colour). */
var TOOTH = 'M24 7c-3-2-7-3-10-2-5 1-8 6-7 12 1 5 3 8 4 13 1 5 2 11 5 11 3 0 3-6 4-9 1-3 2-4 4-4s3 1 4 4c1 3 1 9 4 9 3 0 4-6 5-11 1-5 3-8 4-13 1-6-2-11-7-12-3-1-7 0-10 2z';
window.ICONS = {
  checkup: '<path d="' + TOOTH + '"/><circle cx="34" cy="34" r="7" fill="#fff"/><path d="M39 39l5 5"/>',
  rct: '<path d="' + TOOTH + '"/><path d="M20 16c0 6 0 12-2 20M28 16c0 6 0 12 2 20" stroke-dasharray="2 3"/>',
  fillings: '<path d="' + TOOTH + '"/><path d="M17 13c3 4 11 4 14 0" /><circle cx="24" cy="18" r="2.5" fill="currentColor"/>',
  implants: '<path d="M14 8c3-2 7-2 10 0 3-2 7-2 10 0 3 2 3 7 1 11H13c-2-4-2-9 1-11z"/><path d="M19 19h10l-1 5h-8z"/><path d="M20 24h8l-1 18h-6z"/><path d="M19 29h10M19 34h9M20 39h7"/>',
  crowns: '<path d="M5 14c2-3 6-3 8 0 2-3 6-3 8 0v10H5z"/><path d="M27 14c2-3 6-3 8 0 2-3 6-3 8 0v10H27z"/><path d="M21 17h6M21 21h6"/><path d="M7 24l1 14M19 24l-1 14M29 24l1 14M41 24l-1 14"/>',
  braces: '<path d="' + TOOTH + '"/><rect x="18" y="14" width="12" height="9" rx="2"/><path d="M3 18.5h42"/>',
  cleaning: '<path d="' + TOOTH + '" transform="translate(-4 2) scale(.9)"/><path d="M33 3l1.5 4 4 1.5-4 1.5-1.5 4-1.5-4-4-1.5 4-1.5z" fill="currentColor"/><path d="M41 14l1 2.5 2.5 1-2.5 1-1 2.5-1-2.5-2.5-1 2.5-1z" fill="currentColor"/>',
  whitening: '<path d="' + TOOTH + '"/><path d="M24 2v-2M38 6l2-2M10 6l-2-2M44 18h2M2 18h2"/><path d="M16 14c1-3 4-4 6-4" />',
  cosmetic: '<path d="M6 20c6 12 30 12 36 0"/><path d="M6 20c6 4 30 4 36 0"/><path d="M12 22.5v5M18 23.8v6M24 24v6.5M30 23.8v6M36 22.5v5"/><path d="M37 3l1.5 4 4 1.5-4 1.5L37 14l-1.5-4-4-1.5 4-1.5z" fill="currentColor"/>',
  extraction: '<path d="' + TOOTH + '" transform="translate(0 6)"/><path d="M24 1v9M20 5l4-4 4 4"/>',
  kids: '<path d="' + TOOTH + '"/><circle cx="19" cy="17" r="1.6" fill="currentColor"/><circle cx="29" cy="17" r="1.6" fill="currentColor"/><path d="M19 23c3 3 7 3 10 0"/>',
  dentures: '<path d="M6 30c0-14 8-22 18-22s18 8 18 22"/><path d="M11 30c0-10 6-16 13-16s13 6 13 16"/><path d="M6 30h5M37 30h5M15 19l-3-3M33 19l3-3M24 14V8M19 15l-1.5-5M29 15l1.5-5"/><path d="M4 36h40"/>'
};

window.SERVICES = [
  {
    id: 'checkup', name: 'Dental Check-up & Consultation',
    short: 'A complete examination of your teeth, gums and bite — with X-rays only when needed — to catch problems early and plan the right care.',
    about: 'A thorough check-up is the foundation of good oral health. We examine every tooth, your gums, bite and soft tissues, screen for early decay, gum disease and oral lesions, and explain what we find in simple language — with no pressure to start treatment the same day.',
    signs: ['It has been more than 6 months since your last visit', 'Any pain, sensitivity or bleeding', 'You want a second opinion on a treatment plan'],
    steps: ['Discussion of your concerns and medical history', 'Examination of teeth, gums, tongue and cheeks', 'X-rays only if required for an accurate diagnosis', 'A clear treatment plan with options and a cost estimate'],
    duration: '20–30 minutes',
    care: ['Visit every 6 months, even when nothing hurts', 'Bring any previous dental reports or X-rays']
  },
  {
    id: 'rct', name: 'Root Canal Treatment (RCT & Re-RCT)',
    short: 'Save a badly decayed or infected tooth instead of losing it. Gentle, modern root canal treatment — including re-treatment of failed root canals.',
    about: 'When decay or injury reaches the nerve (pulp) of a tooth, it causes pain, swelling and infection. Root canal treatment removes the infected pulp, cleans and disinfects the canals and seals them — relieving pain while keeping your natural tooth. We also re-treat teeth whose earlier root canal has failed (Re-RCT).',
    signs: ['Severe or lingering toothache', 'Pain on biting or chewing', 'Swelling or a pimple on the gum', 'Sensitivity to hot or cold that does not go away'],
    steps: ['Local anaesthesia so the procedure is comfortable', 'Removal of the infected pulp', 'Cleaning and shaping of the canals', 'Sealing the canals and restoring the tooth', 'A crown to protect the treated tooth'],
    duration: 'Usually 1–3 visits',
    care: ['Avoid chewing hard food on the tooth until the crown is fitted', 'Mild soreness for 1–2 days is normal', 'A crown is strongly recommended for back teeth']
  },
  {
    id: 'fillings', name: 'Tooth-Coloured Fillings',
    short: 'Repair cavities and chipped teeth with natural-looking composite fillings that blend invisibly with your smile.',
    about: 'Cavities caught early can be repaired in a single, comfortable visit. We remove the decay and rebuild the tooth with tooth-coloured composite matched to your natural shade, so the filling is practically invisible.',
    signs: ['Food getting stuck in a tooth', 'A visible dark spot or hole', 'Sensitivity to sweets or cold', 'A small chip or broken edge'],
    steps: ['Shade selection to match your tooth', 'Gentle removal of decay', 'Layer-by-layer bonding of composite', 'Shaping and polishing for a natural bite'],
    duration: '30–45 minutes per tooth, single visit',
    care: ['Eat normally once the numbness wears off', 'Brush twice daily and limit sugary snacks to prevent new cavities']
  },
  {
    id: 'implants', name: 'Dental Implants',
    short: 'A permanent, natural-feeling replacement for missing teeth — a titanium root topped with a lifelike crown.',
    about: 'A dental implant replaces the root of a missing tooth with a small titanium post placed in the jawbone. Once it bonds with the bone, a custom crown is fixed on top. Implants look, feel and work like natural teeth and do not need the neighbouring teeth to be ground down.',
    signs: ['One or more missing teeth', 'Loose or uncomfortable dentures', 'A tooth that cannot be saved'],
    steps: ['Consultation and X-ray assessment of the bone', 'Placement of the implant under local anaesthesia', 'Healing for about 2–4 months while the implant fuses with the bone', 'Fixing the final crown or bridge'],
    duration: '2–4 months from placement to final crown',
    care: ['Brush and floss around the implant like a natural tooth', 'Avoid smoking and tobacco — they slow healing', 'Regular check-ups keep implants healthy for years']
  },
  {
    id: 'crowns', name: 'Crowns & Bridges',
    short: 'Strong, natural-looking ceramic and zirconia caps to protect weak teeth, and fixed bridges to replace missing ones.',
    about: 'A crown (cap) covers and strengthens a tooth that is broken, heavily filled or root-canal treated. A bridge replaces one or more missing teeth by anchoring to the teeth on either side. We offer metal-ceramic, all-ceramic and zirconia options to suit your needs and budget.',
    signs: ['A tooth after root canal treatment', 'A cracked, broken or heavily filled tooth', 'A gap from a missing tooth'],
    steps: ['Tooth preparation and impressions', 'A temporary crown to protect the tooth', 'Custom crown or bridge made in a dental lab', 'Fitting, bite adjustment and cementation'],
    duration: '2 visits, about 5–7 days apart',
    care: ['Avoid very sticky or hard food on a temporary crown', 'Clean under bridges with a floss threader or interdental brush']
  },
  {
    id: 'braces', name: 'Braces & Clear Aligners',
    short: 'Straighten crooked, crowded or gapped teeth with metal braces, ceramic braces or near-invisible clear aligners.',
    about: 'Orthodontic treatment corrects crooked teeth, gaps, crowding and bite problems. It improves your smile, makes teeth easier to clean and reduces uneven wear. We help children, teenagers and adults choose between metal braces, tooth-coloured ceramic braces and clear aligners.',
    signs: ['Crooked, crowded or overlapping teeth', 'Gaps between teeth', 'Upper teeth sticking out', 'Uneven bite'],
    steps: ['Orthodontic assessment with photos and X-rays', 'A personalised plan and timeline', 'Fitting of braces or aligners', 'Regular adjustment visits', 'Retainers to hold teeth in their new position'],
    duration: 'Typically 12–24 months, depending on the case',
    care: ['Avoid hard and sticky foods with braces', 'Wear aligners 20–22 hours a day', 'Wear retainers as advised after treatment']
  },
  {
    id: 'cleaning', name: 'Scaling, Cleaning & Gum Care',
    short: 'Professional cleaning removes plaque and tartar to stop bleeding gums and bad breath, and protect against gum disease.',
    about: 'Even with good brushing, hard tartar builds up where the brush cannot reach. Professional scaling and polishing removes it, freshens breath and protects your gums. For advanced gum disease we offer deep cleaning (root planing) and gum treatment.',
    signs: ['Bleeding gums while brushing', 'Bad breath', 'Yellow or brown deposits near the gums', 'Swollen or receding gums'],
    steps: ['Gum examination', 'Ultrasonic scaling to remove tartar', 'Polishing to remove surface stains', 'Advice on brushing and flossing technique'],
    duration: '30–45 minutes',
    care: ['Mild sensitivity for a day or two is normal', 'Have a professional cleaning every 6 months', 'Use a soft brush and clean between teeth daily']
  },
  {
    id: 'whitening', name: 'Teeth Whitening',
    short: 'Brighten stained or yellow teeth safely by several shades with professional, dentist-supervised whitening.',
    about: 'Tea, coffee, paan, tobacco and age can stain teeth. Professional whitening uses a dentist-controlled gel to safely lighten your teeth by several shades — more effective and safer than over-the-counter products.',
    signs: ['Yellow or dull teeth', 'Stains from tea, coffee or tobacco', 'An upcoming wedding or special occasion'],
    steps: ['Check-up and cleaning to make sure teeth are healthy', 'Recording your current shade', 'Gum protection and whitening gel application', 'Final shade comparison and aftercare advice'],
    duration: 'About 60 minutes, single visit',
    care: ['Avoid tea, coffee, colas and coloured foods for 48 hours', 'Any temporary sensitivity settles quickly']
  },
  {
    id: 'cosmetic', name: 'Smile Design & Veneers',
    short: 'Transform chipped, uneven or discoloured teeth with veneers, bonding and tooth reshaping for a confident smile.',
    about: 'Cosmetic dentistry combines treatments like veneers, composite bonding, tooth reshaping, whitening and gum contouring to create a balanced, natural-looking smile designed around your face.',
    signs: ['Chipped or uneven teeth', 'Small gaps', 'Discoloured teeth that do not respond to whitening', 'Teeth of different sizes'],
    steps: ['Smile analysis and discussion of your goals', 'A preview of the planned result where possible', 'Minimal preparation of the teeth', 'Placement of veneers or bonding', 'Final polish and bite check'],
    duration: '1–3 visits depending on the plan',
    care: ['Avoid biting nails, pens or hard objects', 'Regular check-ups and cleaning keep veneers looking their best']
  },
  {
    id: 'extraction', name: 'Tooth Extraction & Wisdom Teeth',
    short: 'Gentle removal of badly damaged teeth and painful or impacted wisdom teeth, with clear aftercare.',
    about: 'When a tooth cannot be saved, or a wisdom tooth is impacted, infected or causing crowding, a careful extraction relieves pain and prevents further problems. We use local anaesthesia and minimally traumatic techniques for a smoother recovery.',
    signs: ['Pain or swelling at the back of the jaw', 'A tooth broken below the gum line', 'A badly decayed tooth that cannot be restored'],
    steps: ['X-ray to assess the tooth and roots', 'Local anaesthesia', 'Gentle removal (surgical removal for impacted teeth)', 'Stitches if needed, with written aftercare instructions'],
    duration: '20–60 minutes',
    care: ['Bite on the gauze for 30–45 minutes', 'No spitting, straws or smoking for 24 hours', 'Eat soft, cool food on the first day', 'Take medicines exactly as prescribed']
  },
  {
    id: 'kids', name: 'Kids Dentistry',
    short: 'Friendly, gentle dental care for children — check-ups, fluoride, sealants, fillings and baby-tooth treatment.',
    about: 'Healthy baby teeth help children chew, speak and guide adult teeth into place. We keep visits friendly and fear-free, and offer preventive care like fluoride application and pit-and-fissure sealants, along with child-friendly fillings and pulp treatments.',
    signs: ['Your child has never had a dental check-up', 'Black spots or holes in baby teeth', 'Toothache or swelling in a child', 'Thumb-sucking or crooked teeth'],
    steps: ['A friendly introduction to the clinic and instruments', 'Gentle examination and cleaning', 'Fluoride varnish and sealants to prevent cavities', 'Treatment of cavities with child-friendly techniques'],
    duration: '20–40 minutes',
    care: ['First dental visit by age 1 or when the first tooth appears', 'Avoid bedtime milk bottles and frequent sweets', 'Supervise brushing until about age 8']
  },
  {
    id: 'dentures', name: 'Dentures & Full Mouth Rehabilitation',
    short: 'Restore comfortable chewing and a natural smile with complete or partial dentures and full-mouth rehabilitation.',
    about: 'For patients with many missing or worn-down teeth, we plan a complete restoration of function and appearance — using dentures, implant-supported dentures, crowns and bridges — so you can eat, speak and smile with confidence again.',
    signs: ['Many missing teeth', 'Difficulty chewing', 'Worn-down teeth', 'Loose or painful old dentures'],
    steps: ['Detailed examination of teeth, gums, bite and jaw joints', 'A step-by-step treatment plan', 'Impressions and trial fittings', 'Final fitting and adjustments'],
    duration: 'Several visits over a few weeks',
    care: ['Remove and clean dentures daily', 'Do not sleep with dentures unless advised', 'Come in for an adjustment if anything feels loose or sore']
  }
];

/* Treatment finder: symptom → suggested treatments (ids from SERVICES). */
window.SYMPTOMS = [
  { id: 'pain', label: 'Toothache', icon: 'fa-face-frown', services: ['checkup', 'fillings', 'rct'] },
  { id: 'sensitive', label: 'Sensitivity to hot / cold', icon: 'fa-temperature-half', services: ['fillings', 'cleaning', 'checkup'] },
  { id: 'swelling', label: 'Swelling or pus', icon: 'fa-head-side-mask', services: ['rct', 'extraction'], urgent: true },
  { id: 'bleeding', label: 'Bleeding gums / bad breath', icon: 'fa-droplet', services: ['cleaning', 'checkup'] },
  { id: 'missing', label: 'Missing tooth or teeth', icon: 'fa-circle-minus', services: ['implants', 'crowns', 'dentures'] },
  { id: 'crooked', label: 'Crooked or gapped teeth', icon: 'fa-arrows-left-right', services: ['braces', 'cosmetic'] },
  { id: 'stained', label: 'Yellow or stained teeth', icon: 'fa-wand-magic-sparkles', services: ['whitening', 'cleaning', 'cosmetic'] },
  { id: 'broken', label: 'Broken or chipped tooth', icon: 'fa-bolt', services: ['fillings', 'crowns', 'rct'] },
  { id: 'wisdom', label: 'Wisdom tooth pain', icon: 'fa-teeth-open', services: ['extraction', 'checkup'] },
  { id: 'child', label: "My child's teeth", icon: 'fa-child-reaching', services: ['kids'] },
  { id: 'chewing', label: 'Difficulty chewing / loose dentures', icon: 'fa-utensils', services: ['dentures', 'implants'] },
  { id: 'ulcer', label: 'Ulcer or patch not healing (2+ weeks)', icon: 'fa-triangle-exclamation', services: ['checkup'], urgent: true }
];

window.TIPS = [
  { icon: 'fa-tooth', title: 'Brush right, twice a day', text: 'Use a soft brush and fluoride toothpaste for two full minutes, with small circular strokes along the gum line. Replace your brush every 3 months.' },
  { icon: 'fa-grip-lines-vertical', title: 'Clean between your teeth', text: 'A brush cannot reach between teeth, where many cavities and gum problems begin. Use floss or an interdental brush once a day.' },
  { icon: 'fa-candy-cane', title: 'Watch the sugar clock', text: 'How often you eat sugar matters more than how much. Keep sweets to mealtimes and rinse with water after tea, biscuits or snacks.' },
  { icon: 'fa-ban-smoking', title: 'Say no to tobacco & gutka', text: 'Tobacco, gutka and paan masala are leading causes of oral cancer. Any ulcer or white/red patch that lasts over 2 weeks needs a check-up.' },
  { icon: 'fa-snowflake', title: "Don't ignore sensitivity", text: 'Sensitivity can be an early sign of decay, worn enamel or gum recession. A desensitising toothpaste helps, but get it examined.' },
  { icon: 'fa-baby', title: 'First visit by the first birthday', text: "Take your child to the dentist when the first tooth appears. Avoid bedtime milk bottles, and brush your child's teeth for them until about age 8." }
];

/* Each treatment's photo lives at images/treatments/<id>.jpg; replace a file to change its picture. */
window.SERVICES.forEach(function (s) { s.img = 'images/treatments/' + s.id + '.jpg'; });
