"use client";

import { useEffect, useState } from "react";
import styles from "./methodology.module.css";

// Sticky "On this page" index for the long methodology document (audit item 12).
// Replaces the pill-cluster that vanished after the opening viewport. Highlights
// the section currently under the reading line via IntersectionObserver.

export function MethodologyToc({
  items,
  label,
}: {
  items: { id: string; label: string }[];
  label: string;
}) {
  const [active, setActive] = useState<string>(items[0]?.id ?? "");

  useEffect(() => {
    const els = items
      .map((i) => document.getElementById(i.id))
      .filter((el): el is HTMLElement => el !== null);
    if (els.length === 0) return;
    // trigger band sits just under the sticky nav + this bar; a section that
    // enters it becomes active. Works scrolling both directions.
    const obs = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) setActive(e.target.id);
        }
      },
      { rootMargin: "-104px 0px -66% 0px", threshold: 0 },
    );
    els.forEach((el) => obs.observe(el));
    return () => obs.disconnect();
  }, [items]);

  return (
    <nav className={styles.toc} aria-label={label}>
      <span className={styles.tocLabel}>{label}</span>
      <ul className={styles.tocList}>
        {items.map((i) => (
          <li key={i.id}>
            <a
              href={`#${i.id}`}
              className={active === i.id ? styles.tocOn : undefined}
              aria-current={active === i.id ? "true" : undefined}
            >
              {i.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
