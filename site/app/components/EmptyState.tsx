import type { ReactNode } from "react";

// EmptyState (spec §7.8 seed-or-degrade). Any surface whose data is missing
// (no findings, no votes, no matching filters) renders this instead of
// crashing or showing a dead panel. Pages pass translated strings.

export function EmptyState({
  title,
  message,
  icon = "○",
  action,
}: {
  title: ReactNode;
  message?: ReactNode;
  icon?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="emptystate" role="status">
      <span className="emptystate-icon" aria-hidden>
        {icon}
      </span>
      <h3>{title}</h3>
      {message ? <p>{message}</p> : null}
      {action ? <div>{action}</div> : null}
    </div>
  );
}
