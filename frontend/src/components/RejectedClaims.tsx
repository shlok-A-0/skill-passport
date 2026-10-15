import { ArrowUpRight } from "lucide-react";
import { EARLIER_SOURCE_KINDS, type RejectedClaim } from "../api";
import { skillName } from "../skillNames";

export type Audience = "student" | "recruiter";

function ClaimItem({ item }: { item: RejectedClaim }) {
  const claim = item.claim ?? {};
  const earlier = EARLIER_SOURCE_KINDS.has(item.kind ?? "");
  return (
    <article className="rejected-item">
      <strong>
        {earlier && <span className="earlier-source-tag">EARLIER PUBLIC SOURCE</span>}
        {skillName(String(claim.skill_id))} · {String(claim.repo)}/{String(claim.file_path)}
        {claim.start_line ? ` · L${String(claim.start_line)}–L${String(claim.end_line)}` : ""}
      </strong>
      <p>{item.reason}</p>
      {item.failed_checks && !earlier && <small>Failed checks: {item.failed_checks.join(", ")}</small>}
      {item.source_url && <p><a href={item.source_url} target="_blank" rel="noreferrer">View earlier source <ArrowUpRight size={11} /></a></p>}
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
    ? claims.filter((item) => item.kind !== "review" && !EARLIER_SOURCE_KINDS.has(item.kind ?? ""))
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
