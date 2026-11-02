import { Chapters } from "@/components/Chapters";
import { FeatureTabs } from "@/components/FeatureTabs";
import { content } from "@/content";

// Server-rendered page; only Chapters and FeatureTabs hydrate. Sections run in ProductSpec order.
export default function Home() {
  const { hero, story, features, contact } = content;

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
        {/* Hero: animated aurora with no glass over it (glass would re-blur every frame). */}
        <section id="top" className="relative isolate flex min-h-svh flex-col justify-end overflow-hidden px-5 pb-16 pt-40 sm:px-10">
          <div aria-hidden="true" className="aurora absolute inset-0 -z-10">
            <span className="left-[-10%] top-[10%] h-[45vmax] w-[45vmax] bg-[var(--glow-a)]" />
            <span className="right-[-15%] top-[25%] h-[40vmax] w-[40vmax] bg-[var(--glow-b)]" />
            <span className="bottom-[-25%] left-[30%] h-[35vmax] w-[35vmax] bg-[var(--glow-c)]" />
          </div>
          <div className="grid grid-cols-12 gap-x-6">
            <p className="eyebrow rise col-span-12 mb-8">{hero.eyebrow}</p>
            <h1 className="display rise col-span-12 text-[clamp(3.25rem,9.5vw,10rem)] lg:col-span-10" style={{ "--i": 1 } as React.CSSProperties}>
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

        <Chapters eyebrow={story.eyebrow} title={story.title} chapters={story.chapters} />
        <FeatureTabs eyebrow={features.eyebrow} title={features.title} features={features.items} />

        <section id="contact" className="field px-5 py-24 sm:px-10 lg:py-40">
          <div data-reveal className="glass grid grid-cols-12 gap-x-6 gap-y-10 rounded-[2rem] p-8 sm:p-14">
            <p className="eyebrow col-span-12 lg:col-span-3">{contact.eyebrow}</p>
            <div className="col-span-12 lg:col-span-9">
              <h2 className="display text-[clamp(2.5rem,6vw,6rem)]">{contact.headline}</h2>
              <a href={`mailto:${contact.email}`} className="mt-10 inline-flex rounded-full border border-line px-7 py-4 text-sm transition-colors hover:bg-fg hover:text-bg">
                {contact.cta}: {contact.email}
              </a>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-line px-5 py-8 text-sm text-muted sm:px-10">
        © {new Date().getFullYear()} {content.name}
      </footer>
    </>
  );
}
