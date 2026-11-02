// Every word and image on the site comes from here. The build agent replaces this demo with data
// from the job's ProductSpec and lead, then sets `demo: false` (`npm run lint` fails until it does).
// Never invent testimonials, reviews, ratings, client logos, awards, statistics or prices.

export const content = {
  demo: true,
  name: "Meridian",
  description: "A launch site for Meridian, a concept audio device shaped like a drop of water.",
  nav: [
    { label: "Design", href: "#design" },
    { label: "Details", href: "#details" },
    { label: "Contact", href: "#contact" },
  ],
  hero: {
    eyebrow: "Concept · Personal audio",
    headline: "Sound, poured into glass.",
    lede: "Meridian is a speaker without corners: one piece of glass, one driver, and nothing else to look at.",
    cta: { label: "Register interest", href: "#contact" },
  },
  design: {
    eyebrow: "Design",
    statement: "We kept removing parts until the only thing left to design was the light passing through it.",
  },
  details: {
    eyebrow: "Details",
    title: "What's inside the drop.",
    items: [
      { title: "One driver", body: "A single full-range driver, tuned for voices and acoustic music at room volume." },
      { title: "Cast glass shell", body: "The body is one casting, polished by hand, with no seams or screws." },
      { title: "Touch, not buttons", body: "Volume and play live under the surface; the glass is the control." },
    ],
  },
  contact: {
    eyebrow: "Contact",
    headline: "Hear it first.",
    email: "hello@example.com",
  },
};
