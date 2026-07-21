"use client";

import { useEffect, type ReactNode } from "react";
import styles from "./GateOverlay.module.css";

// GateOverlay shell (spec §4.0, §5). The gate is an OVERLAY, never a redirect:
// the real page SSR-renders beneath it (crawlers/OG never see a wall; deep-link
// state is preserved). This shell supplies the blur-under backdrop, the exhibit
// panel, progress dots, and the visible "browse without voting" skip. B1 fills
// the actual blind-pair + choice content via `children`.
//
// Visibility is CSS-driven by `data-gate` on <html> (stamped pre-hydration in
// the layout, spec §4.0): hidden by default (fail-open), shown only when
// data-gate="pending". After the final vote B1 sets `forceHidden` to dismiss.

export function GateOverlay({
  title,
  subtitle,
  done,
  total,
  meta,
  children,
  skipLabel,
  onSkip,
  canSkip = true,
  forceHidden,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  done: number;
  total: number;
  /** optional mono meta row (angle · variant · city · fixture). */
  meta?: ReactNode;
  children: ReactNode;
  skipLabel?: ReactNode;
  /** records nothing, sets the gate done, reveals the page (spec §4.0). */
  onSkip?: () => void;
  /** show the skip once the first pair has rendered (not a buried link). */
  canSkip?: boolean;
  forceHidden?: boolean;
}) {
  const dots = Array.from({ length: Math.max(total, 0) }, (_, i) => i < done);

  // Body scroll-lock while the gate is actually visible (spec §4.0 brief A: the
  // page beneath must read as inert). Visibility is CSS-driven by data-gate on
  // <html> and dismissal flips that attribute imperatively (markGateDone), so we
  // watch the attribute rather than a React prop — locking on "pending" and
  // releasing on "done"/skip. overflow-x is already hidden globally on body;
  // pinning overflow here kills the vertical scroll + any residual x-overflow.
  useEffect(() => {
    const root = document.documentElement;
    const apply = () => {
      const pending = root.getAttribute("data-gate") === "pending" && !forceHidden;
      document.body.style.overflow = pending ? "hidden" : "";
    };
    apply();
    const mo = new MutationObserver(apply);
    mo.observe(root, { attributes: true, attributeFilter: ["data-gate"] });
    return () => {
      mo.disconnect();
      document.body.style.overflow = "";
    };
  }, [forceHidden]);

  return (
    <div
      className="gate-overlay"
      data-force={forceHidden ? "hidden" : undefined}
      role="dialog"
      aria-modal="true"
      aria-label={typeof title === "string" ? title : "arena gate"}
    >
      <div className="gate-backdrop" aria-hidden />
      <div className={`gate-panel panelbox exhibit ${styles.panel}`}>
        <div className="halo" aria-hidden />
        <div className="gate-head">
          <h2>{title}</h2>
          <div className="gate-progress">
            <span>
              {done} / {total}
            </span>
            <span className="gate-dots" aria-hidden>
              {dots.map((filled, i) => (
                <span key={i} className={filled ? "pdot done" : "pdot"} />
              ))}
            </span>
          </div>
        </div>
        {subtitle ? <p className="gate-sub">{subtitle}</p> : null}
        {meta ? <div className={`gate-meta ${styles.meta}`}>{meta}</div> : null}
        {children}
        {onSkip && canSkip && skipLabel ? (
          <div className="gate-skip">
            <button type="button" className={styles.skip} onClick={onSkip}>
              {skipLabel}
            </button>
          </div>
        ) : null}
      </div>
    </div>
  );
}
