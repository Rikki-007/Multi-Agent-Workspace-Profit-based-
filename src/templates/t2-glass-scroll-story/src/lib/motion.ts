// The only place scroll motion is wired up: one Lenis instance driven by GSAP's ticker, plus
// declarative [data-reveal] entrances. Imported by client components only.
import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import Lenis from "lenis";

gsap.registerPlugin(ScrollTrigger);

export { gsap, ScrollTrigger };

export const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export function initSmoothScroll(): () => void {
  if (reducedMotion()) return () => {};
  const lenis = new Lenis({ lerp: 0.1, anchors: true });
  lenis.on("scroll", ScrollTrigger.update);
  const tick = (time: number) => lenis.raf(time * 1000);
  gsap.ticker.add(tick);
  gsap.ticker.lagSmoothing(0);
  return () => {
    gsap.ticker.remove(tick);
    lenis.destroy();
  };
}

// [data-reveal] elements fade and rise once on enter. The initial state is set here, not in CSS,
// so the prerendered HTML stays readable when JS is off or motion is reduced.
export function initReveals(): () => void {
  if (reducedMotion()) return () => {};
  const ctx = gsap.context(() => {
    const items = gsap.utils.toArray<HTMLElement>("[data-reveal]");
    gsap.set(items, { opacity: 0, y: 40 });
    ScrollTrigger.batch(items, {
      start: "top 88%",
      once: true,
      onEnter: (batch) => gsap.to(batch, { opacity: 1, y: 0, duration: 1.1, stagger: 0.08, ease: "expo.out" }),
    });
  });
  return () => ctx.revert();
}
