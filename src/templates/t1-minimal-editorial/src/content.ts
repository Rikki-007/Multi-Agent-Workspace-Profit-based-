// Every word and image on the site comes from here. The build agent replaces this demo with data
// from the job's ProductSpec and lead, then sets `demo: false` (`npm run lint` fails until it does).
// Never invent testimonials, reviews, ratings, client logos, awards, statistics or prices.

export const content = {
  demo: true,
  name: "Halden Studio",
  description: "Architecture and interiors for homes that are lived in hard and kept for decades.",
  nav: [
    { label: "Practice", href: "#practice" },
    { label: "Services", href: "#services" },
    { label: "Contact", href: "#contact" },
  ],
  hero: {
    eyebrow: "Architecture & interiors · Exampletown",
    headline: "Quiet rooms, built with unreasonable care.",
    lede: "A small practice drawing homes, extensions and interiors, from the first sketch to the last fitted joint.",
  },
  practice: {
    eyebrow: "Practice",
    quote: "We draw slowly so that the building doesn't have to be fixed quickly.",
    body: [
      "Every project is led by one of the two partners from the first site visit to handover. There is no hand-off to a junior team.",
      "We work with a short list of builders and makers we trust, and we stay on site until the details are right.",
    ],
    image: { src: "/media/study.svg", alt: "Pale oak study with a single window", width: 1200, height: 1500 },
  },
  services: {
    eyebrow: "Services",
    title: "What we take on",
    items: [
      { title: "New homes", body: "Full design and contract administration for one-off houses." },
      { title: "Extensions", body: "Kitchens, garden rooms and loft conversions that belong to the original house." },
      { title: "Interiors", body: "Joinery, lighting and material schemes, specified to the millimetre." },
      { title: "Planning", body: "Feasibility studies and planning applications, including listed buildings." },
    ],
  },
  contact: {
    eyebrow: "Contact",
    headline: "Tell us about the house.",
    email: "hello@example.com",
    phone: { display: "01234 567890", href: "tel:+441234567890" },
    address: "1 High Street, Exampletown",
  },
};
