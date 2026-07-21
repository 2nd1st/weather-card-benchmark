import type { ReactNode } from "react";

// Section wrapper (spec §5). Two registers: default (exhibit) and `etched`
// (mockup-a hairline legend, for data-dense surfaces like matrix / progress).
// Pure/presentational — pages pass already-translated strings.

export function Section({
  title,
  subtitle,
  aside,
  children,
  etched,
  tag,
  num,
  id,
  className,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  /** right-aligned header slot (e.g. a URL-state readout or a control). */
  aside?: ReactNode;
  children: ReactNode;
  /** restrained hairline-legend variant for tables / matrices. */
  etched?: boolean;
  /** etched legend label (top-left). */
  tag?: string;
  /** etched section number (top-right). */
  num?: string;
  id?: string;
  className?: string;
}) {
  const cls = ["section", etched ? "etched" : "", className ?? ""]
    .filter(Boolean)
    .join(" ");
  return (
    <section id={id} className={cls}>
      {etched && tag ? <span className="sec-tag">{tag}</span> : null}
      {etched && num ? <span className="sec-num">{num}</span> : null}
      {title || subtitle || aside ? (
        <div className="section-head">
          <div>
            {title ? <h2 className="sec">{title}</h2> : null}
            {subtitle ? <p className="sec-sub">{subtitle}</p> : null}
          </div>
          {aside ? <div>{aside}</div> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}
