# T2 Glassmorphism Scroll Story

Tier **T2 Scroll Story**: a prerendered Next.js site (`output: "export"`) that tells its story
through scroll, using Lenis, GSAP ScrollTrigger and Framer Motion. Read `RULES.md` first: it holds
the rules every tier shares, and this site's dependency policy is in `.agency/policy.json` (no
Three.js or other 3D at this tier).

@RULES.md

```bash
npm run dev     # local dev server
npm run lint    # typecheck + scripts/check-rules.mjs
npm run build   # static export to out/
```

## Who animates what

| Job | Tool | Where |
| --- | --- | --- |
| Load-in | CSS `.rise` | `globals.css` (keeps prerendered HTML visible without JS) |
| Smooth scroll | Lenis, exactly one instance [lenis] | `src/lib/motion.ts` `initSmoothScroll`, started by `MotionRoot` |
| Scroll reveals | GSAP ScrollTrigger via `data-reveal` | `initReveals` in `src/lib/motion.ts` |
| Pinned or scrubbed story | GSAP ScrollTrigger with `gsap.matchMedia` | `Chapters.tsx` |
| Component state (tabs, presence, hover) | Framer Motion inside `MotionConfig reducedMotion="user"` | `FeatureTabs.tsx` |

Don't cross the streams: Framer Motion never drives scroll, and GSAP never animates React state.

## Glass rules

- Glass (`.glass`, i.e. `backdrop-filter`) only sits over static backgrounds (`.field`). A
  backdrop-filter over an animated layer, like the aurora, re-blurs every frame and drops frames.
- At most about 3 glass surfaces in the viewport at once.
- Text on glass must still meet AA contrast against the brightest part of the background.

## Scroll story rules

- Pin at most one section per page. Pinned sections use `gsap.matchMedia` so phones, reduced
  motion and no-JS fall back to a plain vertical layout (see `Chapters.tsx`).
- Measure in function-based values (`x: () => …`) with `invalidateOnRefresh`, never per frame.

## Building a site from it

1. Fill `src/content.ts` from the spec and lead, then set `demo: false`.
2. Compose the spec's sections in `src/app/page.tsx`.
3. Run `npm run lint` and `npm run build`.
