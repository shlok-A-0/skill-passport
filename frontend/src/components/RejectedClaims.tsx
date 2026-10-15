import { ArrowUpRight } from "lucide-react";
import type { RejectedClaim } from "../api";
import { skillName } from "../skillNames";

export type Audience = "student" | "recruiter";

function ClaimItem({ item }: { item: RejectedClaim }) {
  const claim = item.claim ?? {};
  return (
    <article className="rejected-item">
      <strong>
        {item.kind === "copied" && <span className="copied-tag">EXCLUDED · COPIED</span>}
        {skillName(String(claim.skill_id))} · {String(claim.repo)}/{String(claim.file_path)}
        {claim.start_line ? ` · L${String(claim.start_line)}–L${String(claim.end_line)}` : ""}
      </strong>
      <p>{item.reason}</p>
      {item.failed_checks && <small>Failed checks: {item.failed_checks.join(", ")}</small>}
      {item.source_url && <p><a href={item.source_url} target="_blank" rel="noreferrer">View original <ArrowUpRight size={11} /></a></p>}
    </article>
  );
}

/**
 * Students see the AI's unsupported claims plus a one-line review summary; recruiters
 * see every rejected and excluded claim with its source.
 */
export default function RejectedClaims({ claims, audience }: { claims: RejectedClaim[]; audience: Audience }) {
  const review = claims.filter((item) => item.kind === "review");
  const listed = audience === "student"
    ? claims.filter((item) => item.kind !== "review" && item.kind !== "copied")
    : claims.filter((item) => item.kind !== "review");
  const title = audience === "student" ? "Rejected AI claims" : "Rejected and excluded claims";
  return (
    <details className="panel rejected-panel" open={audience === "recruiter" && listed.length > 0}>
      <summary>{title} <span className="count-tag">{listed.length}</span></summary>
      {listed.length
        ? listed.map((item, index) => <ClaimItem item={item} key={`${String(item.claim?.file_path)}-${index}`} />)
        : <p className="muted">No proposed claims failed verification in this run.</p>}
      {audience === "student" && review.map((item) => <p className="muted review-summary" key="review">{item.reason}</p>)}
    </details>
  );
}
