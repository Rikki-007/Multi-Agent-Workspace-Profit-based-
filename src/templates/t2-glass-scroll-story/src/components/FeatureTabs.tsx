"use client";

import { AnimatePresence, MotionConfig, motion } from "framer-motion";
import { useState } from "react";

type Feature = { label: string; title: string; body: string };

// Framer Motion's job in T2: state-driven UI motion (scroll motion belongs to GSAP).
// A shared-layout indicator slides between tabs; the panel cross-fades. MotionConfig makes every
// transform respect the visitor's reduced-motion setting.
export function FeatureTabs({ eyebrow, title, features }: { eyebrow: string; title: string; features: Feature[] }) {
  const [active, setActive] = useState(0);
  const feature = features[active];

  return (
    <MotionConfig reducedMotion="user" transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1] }}>
      <section id="features" className="field grid grid-cols-12 gap-x-6 gap-y-12 px-5 py-24 sm:px-10 lg:py-40">
        <div className="col-span-12 lg:col-span-4">
          <p className="eyebrow mb-6">{eyebrow}</p>
          <h2 data-reveal className="display text-[clamp(2.5rem,5vw,5rem)]">
            {title}
          </h2>
        </div>

        <div data-reveal className="glass col-span-12 rounded-3xl p-3 lg:col-span-7 lg:col-start-6">
          <div role="tablist" aria-label={title} className="relative flex flex-wrap gap-1 p-1">
            {features.map((f, i) => (
              <button
                key={f.label}
                role="tab"
                id={`tab-${i}`}
                aria-selected={i === active}
                aria-controls="feature-panel"
                onClick={() => setActive(i)}
                className="relative rounded-full px-4 py-2 text-sm text-muted transition-colors aria-selected:text-bg"
              >
                {i === active && <motion.span layoutId="tab-indicator" className="absolute inset-0 rounded-full bg-fg" />}
                <span className="relative">{f.label}</span>
              </button>
            ))}
          </div>

          <div id="feature-panel" role="tabpanel" aria-labelledby={`tab-${active}`} className="min-h-[14rem] p-6 sm:p-8">
            <AnimatePresence mode="wait" initial={false}>
              <motion.div key={feature.label} initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, y: -12 }}>
                <h3 className="display mb-4 text-3xl sm:text-4xl">{feature.title}</h3>
                <p className="max-w-md leading-relaxed text-muted">{feature.body}</p>
              </motion.div>
            </AnimatePresence>
          </div>
        </div>
      </section>
    </MotionConfig>
  );
}
