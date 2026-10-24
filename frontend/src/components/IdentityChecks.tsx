import { AlertTriangle, Check, CircleHelp } from "lucide-react";
import type { IntegritySignal } from "../api";

/** Recruiter-only: whether the applicant controls the GitHub account and the resume links to it. */
export default function IdentityChecks({ signals }: { signals?: IntegritySignal[] }) {
  if (!signals?.length) return null;
  return (
    <div className="integrity-list">
      <div className="integrity-title">Account checks</div>
      <p className="muted">Shows control of the GitHub account, not real-world identity.</p>
      {signals.map((signal) => (
        <div className={`integrity-row integrity-${signal.status}`} key={signal.id}>
          {signal.status === "ok" ? <Check size={13} /> : signal.status === "warn" ? <AlertTriangle size={13} /> : <CircleHelp size={13} />}
          <span>
            <strong>{signal.label}.</strong>
            {signal.status === "ok" ? (
              signal.detail ? <details className="inline-disclosure"><summary>Details</summary> {signal.detail}</details> : null
            ) : (
              <> {signal.detail}</>
            )}
          </span>
        </div>
      ))}
    </div>
  );
}
