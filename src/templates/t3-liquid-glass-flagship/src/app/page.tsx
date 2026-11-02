import { LiquidGlassBackground } from "@/components/LiquidGlassBackground";
import { content } from "@/content";

// Server-rendered page; only the hero backdrop hydrates (and loads Three.js on capable GPUs).
export default function Home() {
  const { hero, design, details, contact } = content;

  return (
    <>
      <header className="fixed inset-x-0 top-0 z-40 px-5 py-5 sm:px-10">
        <nav aria-label="Primary" className="flex items-center justify-between">
          <a href="#top" className="display text-2xl">
            {content.name}
          </a>
          <ul className="flex gap-6 text-sm text-muted">
            {content.nav.map((item) => (
              <li key={item.href}>
                <a href={item.href} className="transition-colors hover:text-fg">
                  {item.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </header>

      <main>
        <section id="top" className="relative isolate flex min-h-svh flex-col justify-end overflow-hidden px-5 pb-16 pt-40 sm:px-10">
          <LiquidGlassBackground />
          <div className="grid grid-cols-12 gap-x-6">
            <p className="eyebrow rise col-span-12 mb-8">{hero.eyebrow}</p>
            <h1 className="display rise col-span-12 text-[clamp(3.5rem,11vw,12rem)] xl:col-span-10" style={{ "--i": 1 } as React.CSSProperties}>
              {hero.headline}
            </h1>
            <div className="rise col-span-12 mt-12 flex flex-col gap-8 border-t border-line pt-6 sm:flex-row sm:items-end sm:justify-between" style={{ "--i": 2 } as React.CSSProperties}>
              <p className="max-w-md text-lg leading-relaxed text-muted">{hero.lede}</p>
              <a href={hero.cta.href} className="rounded-full bg-fg px-7 py-4 text-sm font-medium text-bg transition-transform hover:-translate-y-0.5">
                {hero.cta.label}
              </a>
            </div>
          </div>
        </section>

        <section id="design" className="grid grid-cols-12 gap-x-6 border-t border-line px-5 py-28 sm:px-10 lg:py-48">
          <p className="eyebrow col-span-12 mb-8 lg:col-span-3">{design.eyebrow}</p>
          <p data-reveal className="display col-span-12 text-[clamp(2.25rem,5vw,5.25rem)] lg:col-span-9">
            {design.statement}
          </p>
        </section>

        {/* Details: one tall panel against two stacked ones, offset on the grid. */}
        <section id="details" className="px-5 pb-28 sm:px-10 lg:pb-48">
          <h2 data-reveal className="display mb-14 text-[clamp(2.5rem,5.5vw,5.5rem)]">
            {details.title}
          </h2>
          <ol className="grid grid-cols-12 gap-6">
            {details.items.map((item, i) => (
              <li
                key={item.title}
                data-reveal
                className={`panel flex flex-col justify-between gap-10 rounded-3xl p-8 ${
                  i === 0 ? "col-span-12 min-h-[24rem] lg:col-span-7 lg:row-span-2" : "col-span-12 lg:col-span-5"
                }`}
              >
                <span className="font-mono text-sm text-accent">{String(i + 1).padStart(2, "0")}</span>
                <div>
                  <h3 className={`display mb-4 ${i === 0 ? "text-5xl" : "text-3xl"}`}>{item.title}</h3>
                  <p className="max-w-sm leading-relaxed text-muted">{item.body}</p>
                </div>
              </li>
            ))}
          </ol>
        </section>

        <section id="contact" className="grid grid-cols-12 gap-x-6 gap-y-10 border-t border-line px-5 py-28 sm:px-10 lg:py-40">
          <p className="eyebrow col-span-12 lg:col-span-3">{contact.eyebrow}</p>
          <div className="col-span-12 lg:col-span-9">
            <h2 data-reveal className="display text-[clamp(3rem,8vw,8rem)]">
              {contact.headline}
            </h2>
            <a href={`mailto:${contact.email}`} className="mt-10 inline-flex rounded-full bg-fg px-7 py-4 text-sm font-medium text-bg transition-transform hover:-translate-y-0.5">
              {contact.email}
            </a>
          </div>
        </section>
      </main>

      <footer className="border-t border-line px-5 py-8 text-sm text-muted sm:px-10">
        © {new Date().getFullYear()} {content.name}
      </footer>
    </>
  );
}
