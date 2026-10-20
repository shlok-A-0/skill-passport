import { ArrowUpRight, Check, MessageSquare } from "lucide-react";
import { useState } from "react";
import {
  api, citationKey, DEFAULT_CHECK_LABELS, EXCLUDED_KINDS, NOT_WRITTEN_KIND, type RejectedClaim, type ReviewRequest,
} from "../api";
import { skillName } from "../skillNames";

export type Audience = "student" | "recruiter";

function isExcluded(item: RejectedClaim): boolean {
  return EXCLUDED_KINDS.has(item.kind ?? "");
}

function Where({ item, tag }: { item: RejectedClaim; tag?: string }) {
  const claim = item.claim ?? {};
  return (
    <strong>
      {tag && <span className="earlier-source-tag">{tag}</span>}
      {skillName(String(claim.skill_id))} · {String(claim.repo)}/{String(claim.file_path)}
      {claim.start_line ? ` · L${String(claim.start_line)}–L${String(claim.end_line)}` : ""}
    </strong>
  );
}

function Links({ item, ownLabel }: { item: RejectedClaim; ownLabel: string }) {
  if (!item.url && !item.source_url) return null;
  return (
    <p className="rejected-links">
      {item.url && <a href={item.url} target="_blank" rel="noreferrer">{ownLabel} <ArrowUpRight size={11} /></a>}
      {item.source_url && <a href={item.source_url} target="_blank" rel="noreferrer">View earlier source <ArrowUpRight size={11} /></a>}
    </p>
  );
}

/** Student: an excluded citation with a "Request review" form. */
function StudentExcluded({ item, passportId }: { item: RejectedClaim; passportId: string }) {
  const ownsPassport = api.hasPassportToken(passportId);
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState("");
  const [requested, setRequested] = useState(item.review_request?.status === "open");

  async function send() {
    if (!item.citation_key) return;
    setSending(true);
    setError("");
    try {
      await api.requestReview(passportId, item.citation_key, note);
      setRequested(true);
      setOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not send the request.");
    } finally {
      setSending(false);
    }
  }

  return (
    <article className="rejected-item">
      <Where item={item} tag="NOT COUNTED" />
      <p>{item.reason}</p>
      <Links item={item} ownLabel="Your lines" />
      {requested ? (
        <p className="review-status"><Check size={12} /> Review requested</p>
      ) : open ? (
        <div className="review-form">
          <textarea maxLength={1000} rows={3} value={note} onChange={(event) => setNote(event.target.value)}
            placeholder="Optional: explain where this code comes from (for example, an earlier project under another account)." />
          <div>
            <button className="button button-light" onClick={send} disabled={sending}>{sending ? "Sending…" : "Send request"}</button>
            <button className="link-button" onClick={() => setOpen(false)} disabled={sending}>Cancel</button>
          </div>
          {error && <p className="notice-error">{error}</p>}
        </div>
      ) : (
        item.citation_key && (ownsPassport
          ? <button className="button button-light review-button" onClick={() => setOpen(true)}><MessageSquare size={13} /> Request review</button>
          : <p className="muted">Review requests must be submitted from the browser session that created this passport.</p>)
      )}
    </article>
  );
}

/** Recruiter: an excluded citation, with the student's request (if any) and "Mark reviewed". */
function RecruiterExcluded({ item, request, onChanged }: { item: RejectedClaim; request?: ReviewRequest; onChanged?: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function markReviewed() {
    if (!request) return;
    setBusy(true);
    setError("");
    try {
      await api.markReviewed(request.id);
      onChanged?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update the request.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <article className="rejected-item">
      <Where item={item} tag={item.kind === NOT_WRITTEN_KIND ? "NOT WRITTEN BY CANDIDATE" : "EARLIER PUBLIC SOURCE"} />
      <p>{item.reason}</p>
      <Links item={item} ownLabel="Candidate's lines" />
      {request && (
        <div className={`review-request review-${request.status}`}>
          <strong>Candidate requested a review · {new Date(request.created_at).toLocaleDateString()}</strong>
          {request.note ? <p>“{request.note}”</p> : <p className="muted">No note added.</p>}
          {request.status === "open"
            ? <button className="button button-light" onClick={markReviewed} disabled={busy}>{busy ? "Saving…" : "Mark reviewed"}</button>
            : <p className="review-status"><Check size={12} /> Reviewed {request.reviewed_at ? new Date(request.reviewed_at).toLocaleDateString() : ""}</p>}
          {error && <p className="notice-error">{error}</p>}
        </div>
      )}
    </article>
  );
}

/** The backend's reason already lists the failed checks, so each one is shown once, by label. */
function UnsupportedItem({ item, labels }: { item: RejectedClaim; labels?: Record<string, string> }) {
  const failed = [...new Set(item.failed_checks ?? [])];
  return (
    <article className="rejected-item">
      <Where item={item} />
      {failed.length
        ? <p>Failed checks: {failed.map((key) => labels?.[key] ?? DEFAULT_CHECK_LABELS[key] ?? key).join(", ")}</p>
        : <p>{item.reason}</p>}
    </article>
  );
}

/**
 * Students see each citation that wasn't counted (with "Request review") and the AI's
 * unsupported claims; recruiters see every rejected and excluded claim, with review requests.
 */
export default function RejectedClaims({ claims, audience, passportId = "", requests = [], onChanged, labels }: {
  claims: RejectedClaim[];
  audience: Audience;
  passportId?: string;
  requests?: ReviewRequest[];
  onChanged?: () => void;
  labels?: Record<string, string>;
}) {
  // "review" is the summary line older backends sent instead of individual items.
  const listed = claims.filter((item) => item.kind !== "review");
  const excluded = listed.filter(isExcluded);
  const latestRequest = (item: RejectedClaim) => {
    const key = citationKey(item.claim ?? {});
    return [...requests].reverse().find((request) => request.citation_key === key);
  };
  const title = audience === "student" ? "Claims not counted" : "Rejected and excluded claims";
  return (
    <details className="panel rejected-panel" open={excluded.length > 0 || (audience === "recruiter" && listed.length > 0)}>
      <summary>{title} <span className="count-tag">{listed.length}</span></summary>
      {listed.length ? listed.map((item, index) => {
        const key = `${String(item.claim?.file_path)}-${index}`;
        if (!isExcluded(item)) return <UnsupportedItem item={item} labels={labels} key={key} />;
        return audience === "student"
          ? <StudentExcluded item={item} passportId={passportId} key={key} />
          : <RecruiterExcluded item={item} request={latestRequest(item)} onChanged={onChanged} key={key} />;
      }) : <p className="muted">No proposed claims failed verification in this run.</p>}
      {claims.filter((item) => item.kind === "review").map((item) => <p className="muted review-summary" key="review">{item.reason}</p>)}
    </details>
  );
}
