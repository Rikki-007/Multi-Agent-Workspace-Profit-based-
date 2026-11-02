"use client";

import { useGSAP } from "@gsap/react";
import { useRef } from "react";
import { gsap } from "@/lib/motion";

type Chapter = { title: string; body: string };

// The scroll story: on wide screens the section pins and its glass panels travel sideways with
// the scroll. Phones, reduced motion and no-JS get the same panels as a plain vertical stack.
export function Chapters({ eyebrow, title, chapters }: { eyebrow: string; title: string; chapters: Chapter[] }) {
  const ref = useRef<HTMLElement>(null);

  useGSAP(
    () => {
      const mm = gsap.matchMedia();
      mm.add("(min-width: 768px) and (prefers-reduced-motion: no-preference)", () => {
        const section = ref.current!;
        const track = section.querySelector<HTMLElement>("[data-track]")!;
        track.classList.add("is-horizontal");
        const distance = () => track.scrollWidth - window.innerWidth; // measured on refresh only
        gsap.to(track, {
          x: () => -distance(),
          ease: "none",
          scrollTrigger: { trigger: section, start: "top top", end: () => `+=${distance()}`, pin: true, scrub: 1, invalidateOnRefresh: true },
        });
        return () => track.classList.remove("is-horizontal");
      });
      return () => mm.revert();
    },
    { scope: ref },
  );

  return (
    <section ref={ref} id="story" className="field overflow-hidden py-24 md:flex md:min-h-svh md:flex-col md:justify-center">
      <div className="mb-14 grid grid-cols-12 gap-x-6 px-5 sm:px-10">
        <p className="eyebrow col-span-12 mb-4 lg:col-span-3">{eyebrow}</p>
        <h2 className="display col-span-12 text-[clamp(2.5rem,5.5vw,5.5rem)] lg:col-span-8">{title}</h2>
      </div>
      <ol data-track className="story-track flex flex-col gap-6 px-5 sm:px-10">
        {chapters.map((c, i) => (
          <li key={c.title} className="glass flex min-h-[22rem] w-full flex-col justify-between rounded-3xl p-8 md:w-[min(34rem,70vw)]">
            <span className="font-mono text-sm text-accent">{String(i + 1).padStart(2, "0")}</span>
            <div>
              <h3 className="display mb-4 text-4xl">{c.title}</h3>
              <p className="max-w-sm leading-relaxed text-muted">{c.body}</p>
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
