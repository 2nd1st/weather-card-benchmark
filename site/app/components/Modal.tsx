"use client";

import { useEffect, useRef, type ReactNode } from "react";

// Modal (spec §5). Generic dialog shell used by compare's config picker, the
// share sheet, etc. Backdrop click + Escape close; focus is moved into the
// panel on open. Presentational — callers own the content and open state.

export function Modal({
  open,
  onClose,
  title,
  children,
  labelledBy,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  labelledBy?: string;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    panelRef.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div
        className="modal panelbox"
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        ref={panelRef}
        onMouseDown={(e) => e.stopPropagation()}
      >
        {title ? (
          <div className="modal-head">
            <h2 id={labelledBy}>{title}</h2>
            <button className="modal-close" onClick={onClose} aria-label="close">
              ✕
            </button>
          </div>
        ) : null}
        {children}
      </div>
    </div>
  );
}
