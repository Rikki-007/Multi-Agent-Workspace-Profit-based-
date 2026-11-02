# T1 Minimalist Editorial

Tier **T1 Editorial**: a static, server-rendered Next.js site (`output: "export"`) with Tailwind and
**CSS-only motion**. Read `RULES.md` first: it holds the rules every tier shares, and this site's
dependency policy is in `.agency/policy.json`.

@RULES.md

```bash
npm run dev     # local dev server
npm run lint    # typecheck + scripts/check-rules.mjs
npm run build   # static export to out/
```

## What T1 means

- No JavaScript animation or 3D libraries. GSAP, Lenis, Framer Motion and Three.js are forbidden and
  the build is rejected if any appear, even transitively.
- Keep pages as server components. Add `"use client"` only for a real interaction (for example a
  menu toggle), never for motion.
- Motion is CSS, from `src/app/globals.css`:
  - `.rise` with `style={{ "--i": n }}` for the staggered load-in.
  - `.reveal` and `.reveal-media` for scroll-driven entrances (`animation-timeline: view()`, inside
    `@supports`).

  Unsupported browsers and reduced-motion users get static, fully visible content.
- Hover states use the `.ink` underline (transform only).

## Building a site from it

1. Fill `src/content.ts` from the spec and lead, then set `demo: false`.
2. Compose the spec's sections in `src/app/page.tsx`. Each section is a plain `<section>` on the
   12-column grid; copy the pattern of an existing one rather than adding components.
3. Replace `public/media/` art with real or honest abstract images (width/height/alt always).
4. Run `npm run lint` and `npm run build`.
