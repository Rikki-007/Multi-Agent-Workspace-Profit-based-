# Site rules (all tiers)

Shared by every agency template; vendored from `src/templates/_shared/` (edit it there, then run
`python -m agency.templates sync`). `npm run lint` enforces the measurable rules (names in
brackets). A site is done when `npm run lint` and `npm run build` both pass.

## Dependency policy

This site belongs to one tier: T1 Editorial, T2 Scroll Story or T3 Flagship 3D. Its exact
`allowed_deps` and `forbidden_deps` are in `.agency/policy.json` (written when the job workspace
is created; the source is `src/templates/registry.json`). The harness rejects any build whose
`package.json` or lockfile breaks them, including heavy packages pulled in transitively. Never add
a dependency to get an effect the tier's stack can already produce. If a site truly needs more,
it belongs in a higher tier; say so in your summary instead of installing it.

## Content honesty: never look like a template or a scam

- Every fact comes from the spec or the lead: name, address, phone, email, services and prices.
- Never invent testimonials, reviews, star ratings, client logos, awards, years in business,
  statistics, "trusted by N" lines, countdowns or fake scarcity [unverified-claim]. If the spec
  holds a real figure, cite its source in a `rules-allow: unverified-claim` comment.
- If information is missing, leave the section out. Never use lorem ipsum or placeholder names
  [placeholder-copy]. Fill `src/content.ts` and set `demo: false` [demo-content].
- Images are client-provided or honest abstract or texture art. Never use stock photos posing as
  the client's team or work.

## Typography and layout

- An editorial serif display face against a tight grotesque body, loaded with `next/font`
  [self-host-fonts]. Display text uses large fluid `clamp()` sizes, leading 0.9–1.0 and negative
  tracking. Body is 16–18px, lines at most ~65ch.
- Use a 12-column grid with deliberate asymmetry: unequal spans and offset starts. No uniform 3- or
  4-up card grids [uniform-grid], no icon-title-text trios, no centre-aligned everything. Keep
  whitespace generous and use hairline rules for edges.
- Colour comes from the CSS variables in `src/app/globals.css`, never raw hex in components.

## Motion and performance

- Animate only `transform` and `opacity` [transform-only]. That covers CSS transitions, keyframes,
  GSAP tweens and Framer Motion props. Colour transitions on hover are fine.
- No raw scroll listeners [scroll-listener]: use CSS scroll-driven animation, ScrollTrigger, or
  `whileInView`.
- Respect `prefers-reduced-motion` everywhere [reduced-motion]: content must be static and readable
  with motion off, or with JS off.
- Measure layout on enter or resize, never per frame or per pointer move.
- Every `<img>`/`<Image>` has `alt` and either `width` and `height` or `fill` [img-attrs].
- Lighthouse mobile targets: performance ≥90; accessibility, best practices and SEO 100; CLS 0.

## Accessibility

Use semantic landmarks, exactly one `<h1>` per page, visible focus, WCAG AA contrast (≥3:1 for
large or dimmed text), and alt text that describes the image. Decorative layers (canvases,
gradients, grain) get `aria-hidden="true"`.

## Code discipline

- Make the smallest change that works: edit `src/content.ts` and the page first, change a
  component only when it needs to.
- No wrappers, registries, config layers, or helpers with one call site. Comments say why, not
  what.
- When something breaks: reproduce it, read the real error, and trace the values step by step.
  Fix the cause and re-run lint and build. Never make speculative rewrites or swallow errors.
- A rule can be waived for one line with `rules-allow: <rule>` plus the reason. Waivers are
  reviewed.
