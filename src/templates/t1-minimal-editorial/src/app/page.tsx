import { content } from "@/content";

// One page, server-rendered, no client components: T1 sites ship no JavaScript of their own.
// Sections run in ProductSpec.sections order; add, remove or reorder them here.
export default function Home() {
  const { hero, practice, services, contact } = content;

  return (
    <>
      <header className="border-b border-line px-5 sm:px-10">
        <nav aria-label="Primary" className="flex items-baseline justify-between py-5">
          <a href="#top" className="display text-2xl">
            {content.name}
          </a>
          <ul className="flex gap-6 text-sm">
            {content.nav.map((item) => (
              <li key={item.href}>
                <a href={item.href} className="ink">
                  {item.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>
      </header>

      <main id="top">
        {/* Hero: meta column on the left, oversized serif headline offset to the right. */}
        <section className="grid grid-cols-12 gap-x-6 gap-y-10 px-5 pb-24 pt-20 sm:px-10 lg:pb-40 lg:pt-32">
          <p className="eyebrow rise col-span-12 lg:col-span-3">{hero.eyebrow}</p>
          <h1 className="display rise col-span-12 text-[clamp(3.25rem,9vw,9.5rem)] lg:col-span-9" style={{ "--i": 1 } as React.CSSProperties}>
            {hero.headline}
          </h1>
          <p
            className="rise col-span-12 max-w-md text-lg leading-relaxed text-muted lg:col-span-4 lg:col-start-4"
            style={{ "--i": 2 } as React.CSSProperties}
          >
            {hero.lede}
          </p>
        </section>

        {/* Practice: sticky pull quote against an offset column of text and a framed image. */}
        <section id="practice" className="grid grid-cols-12 gap-x-6 gap-y-16 border-t border-line px-5 py-24 sm:px-10 lg:py-40">
          <div className="col-span-12 lg:col-span-5">
            <div className="lg:sticky lg:top-16">
              <p className="eyebrow mb-8">{practice.eyebrow}</p>
              <blockquote className="display reveal text-[clamp(2.25rem,4.5vw,4.5rem)]">“{practice.quote}”</blockquote>
            </div>
          </div>
          <div className="col-span-12 lg:col-span-6 lg:col-start-7">
            <div className="reveal max-w-md space-y-5 leading-relaxed text-muted">
              {practice.body.map((p) => (
                <p key={p}>{p}</p>
              ))}
            </div>
            <figure className="reveal-media mt-16 aspect-[4/5] overflow-hidden border border-line lg:mt-24">
              <img src={practice.image.src} alt={practice.image.alt} width={practice.image.width} height={practice.image.height} loading="lazy" className="h-full w-full object-cover" />
            </figure>
          </div>
        </section>

        {/* Services: a numbered editorial index, not a card grid. */}
        <section id="services" className="border-t border-line px-5 py-24 sm:px-10 lg:py-40">
          <div className="mb-16 grid grid-cols-12 gap-x-6">
            <p className="eyebrow col-span-12 mb-6 lg:col-span-3">{services.eyebrow}</p>
            <h2 className="display reveal col-span-12 text-[clamp(2.5rem,5vw,5rem)] lg:col-span-9">{services.title}</h2>
          </div>
          <ol className="border-t border-line">
            {services.items.map((item, i) => (
              <li key={item.title} className="reveal grid grid-cols-12 items-baseline gap-x-6 border-b border-line py-8">
                <span className="col-span-2 font-mono text-sm text-muted lg:col-span-3">{String(i + 1).padStart(2, "0")}</span>
                <h3 className="display col-span-10 text-3xl lg:col-span-4 lg:text-4xl">{item.title}</h3>
                <p className="col-span-10 col-start-3 mt-3 max-w-md leading-relaxed text-muted lg:col-span-5 lg:col-start-8 lg:mt-0">{item.body}</p>
              </li>
            ))}
          </ol>
        </section>

        {/* Contact: the email address is the call to action. */}
        <section id="contact" className="grid grid-cols-12 gap-x-6 gap-y-12 border-t border-line px-5 py-24 sm:px-10 lg:py-40">
          <p className="eyebrow col-span-12 lg:col-span-3">{contact.eyebrow}</p>
          <div className="col-span-12 lg:col-span-9">
            <h2 className="display reveal text-[clamp(2.75rem,7vw,7rem)]">{contact.headline}</h2>
            <a href={`mailto:${contact.email}`} className="ink display mt-10 inline-block text-[clamp(1.75rem,3.5vw,3rem)] text-accent">
              {contact.email}
            </a>
            <dl className="mt-16 flex flex-wrap gap-x-16 gap-y-6 border-t border-line pt-8">
              <div>
                <dt className="eyebrow mb-2">Phone</dt>
                <dd>
                  <a href={contact.phone.href} className="ink">
                    {contact.phone.display}
                  </a>
                </dd>
              </div>
              <div>
                <dt className="eyebrow mb-2">Studio</dt>
                <dd>{contact.address}</dd>
              </div>
            </dl>
          </div>
        </section>
      </main>

      <footer className="flex flex-wrap justify-between gap-4 border-t border-line px-5 py-8 text-sm text-muted sm:px-10">
        <p>
          © {new Date().getFullYear()} {content.name}
        </p>
        <a href="#top" className="ink">
          Back to top
        </a>
      </footer>
    </>
  );
}
