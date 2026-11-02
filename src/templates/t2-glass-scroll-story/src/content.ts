// Every word and image on the site comes from here. The build agent replaces this demo with data
// from the job's ProductSpec and lead, then sets `demo: false` (`npm run lint` fails until it does).
// Never invent testimonials, reviews, ratings, client logos, awards, statistics or prices.

export const content = {
  demo: true,
  name: "Aster",
  description: "Bookkeeping software for small teams: invoices, receipts and reports that keep themselves in order.",
  nav: [
    { label: "Story", href: "#story" },
    { label: "Features", href: "#features" },
    { label: "Contact", href: "#contact" },
  ],
  hero: {
    eyebrow: "Bookkeeping for small teams",
    headline: "Your books, quietly in order.",
    lede: "Aster collects invoices and receipts, matches them to your bank, and leaves you with reports you can read.",
    cta: { label: "Book a demo", href: "#contact" },
  },
  story: {
    eyebrow: "How it works",
    title: "From shoebox to statement in four steps.",
    chapters: [
      { title: "Collect", body: "Forward receipts by email or snap them on your phone. Aster files them as they arrive." },
      { title: "Match", body: "Every bank line is paired with its receipt, and anything unmatched is flagged for you." },
      { title: "Review", body: "Check the week in one screen and approve it with a single pass." },
      { title: "Report", body: "Profit and loss, VAT and cash flow, ready for you or your accountant." },
    ],
  },
  features: {
    eyebrow: "Features",
    title: "Built for the person who does the books on Friday.",
    items: [
      { label: "Receipts", title: "Snap it, forget it", body: "Photos and PDFs are read, categorised and attached to the right transaction." },
      { label: "Bank feeds", title: "Always reconciled", body: "Connected accounts sync daily, so the numbers you see are this morning's." },
      { label: "Reports", title: "Readable reports", body: "Plain-language summaries next to the figures, exportable for your accountant." },
    ],
  },
  contact: {
    eyebrow: "Contact",
    headline: "See it with your own numbers.",
    email: "hello@example.com",
    cta: "Email the team",
  },
};
