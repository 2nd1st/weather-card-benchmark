// Landing hero exhibit visual (spec §5 exhibit-glow). Decorative, desktop-only
// (hidden <1024px via .hero-exhibit CSS). Renders up to 3 of the daily featured
// shots as overlapping, slightly-rotated cards with soft radial halos — a static
// transform-only composition, so it needs no prefers-reduced-motion handling.
// aria-hidden: purely presentational, adds nothing for assistive tech.

export function HeroExhibit({ shots }: { shots: string[] }) {
  const cards = shots.filter(Boolean).slice(0, 3);
  if (cards.length === 0) return null;
  return (
    <div className="hero-exhibit" aria-hidden>
      {cards.map((src, i) => (
        <div className={`hx-card hx-card-${i + 1}`} key={`${i}-${src}`}>
          <span className="hx-halo" />
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={src} alt="" loading="lazy" />
        </div>
      ))}
    </div>
  );
}
