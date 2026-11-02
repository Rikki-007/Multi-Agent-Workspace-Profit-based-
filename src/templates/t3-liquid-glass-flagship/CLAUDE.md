# T3 Flagship Liquid Glass

Tier **T3 Flagship 3D**: a prerendered Next.js site (`output: "export"`) with a WebGL liquid-glass
backdrop on top of the T2 scroll stack (Lenis and GSAP). Read `RULES.md` first: it holds the rules
every tier shares, and this site's dependency policy is in `.agency/policy.json`. Three.js is
allowed; a second 3D engine or scene editor (Spline, Babylon, etc.) is not.

@RULES.md

```bash
npm run dev     # local dev server
npm run lint    # typecheck + scripts/check-rules.mjs
npm run build   # static export to out/
```

## The WebGL contract

Every 3D layer follows the pattern in `LiquidGlassBackground.tsx`:

1. **Poster first.** A static CSS poster (`.liquid-poster`) is always rendered. It's the whole
   experience for no-JS, reduced-motion, Save-Data and low-GPU visitors, so it has to look
   finished on its own.
2. **Tier before download.** `detectGpuTier()` (`src/lib/gpu.ts`) returns `none`, `low` or `high`
   before any 3D code loads. Software rasterisers count as `none`.
3. **Lazy Three.js.** The scene module is pulled in with `import()` during idle time, never
   imported statically from a page or layout, so it stays out of the initial bundle.
4. **Budgets.** Render at most 0.6× DPR (`low`: 0.35× and 30 fps), cap DPR at 1.5, and use one
   context and one draw call where possible.
5. **Only while seen.** Render only while the hero is on screen and the tab is visible.
6. **Watchdog.** If frames run slow, step high → low → poster. Never keep a struggling device on
   the shader.
7. **Clean up.** Dispose geometry, material and renderer, and force the context loss on unmount.

Panels use `.panel` ("solid glass"), never `backdrop-filter`: blurring a live shader re-renders the
blur every frame.

## Motion

Lenis and GSAP work as in T2 (`src/lib/motion.ts`, `MotionRoot`, `data-reveal`). Load-in is CSS
`.rise`. Framer Motion is allowed at this tier but isn't installed; add it only for state-driven UI
that GSAP doesn't cover.

## Building a site from it

1. Fill `src/content.ts` from the spec and lead, then set `demo: false`.
2. Retune the shader colours with the CSS variables (`--glow-a`, `--glow-b`, `--accent`). Keep the
   poster's gradients matched to them.
3. Run `npm run lint` and `npm run build`.
