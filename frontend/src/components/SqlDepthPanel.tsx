import { AlertTriangle, CheckCircle2, Database, ExternalLink } from "lucide-react";
import { useEffect, useState } from "react";
import { api, type SqlDepth, type SqlEvidence } from "../api";
import {
  evidenceWhere, partPoints, partRows, SQL_DEPTH_MISSING, SQL_DEPTH_NOTE, SQL_DEPTH_UNAVAILABLE,
  sqlDepthHeadline, sqlDepthWarnings,
} from "../sqlDepth";
import type { Audience } from "./RejectedClaims";

function EvidenceLink({ item }: { item: SqlEvidence }) {
  return (
    <a className="sql-evidence-link" href={item.url} target="_blank" rel="noreferrer" title={item.snippet || undefined}>
      {evidenceWhere(item)}{item.detail ? ` · ${item.detail}` : ""} <ExternalLink size={10} />
    </a>
  );
}

const copyCheckLabels: Record<NonNullable<SqlEvidence["copy_check"]>, string> = {
  no_match: "No earlier public source found",
  advisory_match: "One line matches public code (advisory)",
  not_checked: "Earlier-source check not run on these lines",
};

/**
 * SQL evidence depth, point by point. Every part shows the points it earned, why, and the lines
 * that earned them. `compact` (recruiter summary) starts collapsed so the page fits one screen.
 */
export default function SqlDepthPanel({ result, audience, compact = false }: {
  result: SqlDepth | null | undefined;
  audience: Audience;
  compact?: boolean;
}) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  useEffect(() => {
    if (result !== undefined) return;
    // Older passports have no result: say so only when the feature is on.
    let active = true;
    api.features().then((features) => { if (active) setEnabled(Boolean(features.sql_depth)); }).catch(() => undefined);
    return () => { active = false; };
  }, [result]);

  if (result === undefined) return enabled ? <p className="unavailable-note">{SQL_DEPTH_MISSING}</p> : null;
  if (result === null) return <p className="unavailable-note">{SQL_DEPTH_UNAVAILABLE}</p>;

  const recruiter = audience === "recruiter";
  const warnings = sqlDepthWarnings(result);
  return (
    <details className="panel interview-summary sql-depth-panel" open={!compact}>
      <summary>
        <span><strong>{sqlDepthHeadline(result)}</strong><small className="sql-depth-summary-note">Patterns in sampled files, not overall skill.</small></span>
        <Database size={17} color="#c95118" />
      </summary>
      <p className="sql-depth-note">{SQL_DEPTH_NOTE}</p>
      {warnings.map((warning) => <p className="unavailable-note" key={warning}><AlertTriangle size={11} /> {warning}</p>)}
      <ol className="sql-parts">
        {partRows(result).map((part) => (
          <li key={part.key}>
            <div className="sql-part-head"><strong>{part.label}</strong><span>{partPoints(part)}</span></div>
            <div className="sql-part-bar" aria-hidden="true"><i style={{ width: `${part.max_points ? (part.points / part.max_points) * 100 : 0}%` }} /></div>
            <p>{part.reason}</p>
            {part.evidence.map((item) => <EvidenceLink key={`${item.url}-${item.detail ?? ""}`} item={item} />)}
          </li>
        ))}
      </ol>
      <div className="sql-concepts">
        <h3>Concepts found ({result.concepts_found.length} of {result.concepts_found.length + result.concepts_missing.length})</h3>
        <ul>
          {result.concepts_found.map((concept) => (
            <li key={concept.id}>
              <span className="flag-ok"><CheckCircle2 size={11} /> {concept.label}</span>
              {concept.evidence.slice(0, 2).map((item) => <EvidenceLink key={item.url} item={item} />)}
            </li>
          ))}
        </ul>
        {result.concepts_missing.length > 0 && (
          <>
            <h3>Not found in the analysed files</h3>
            <div className="claimed-inline">{result.concepts_missing.map((concept) => <span key={concept.id}>{concept.label}</span>)}</div>
          </>
        )}
      </div>
      {result.distinct_queries.length > 0 && (
        <details className="sql-queries">
          <summary>Distinct queries ({result.distinct_query_count})</summary>
          {result.distinct_queries.map((query, index) => (
            <article className="sql-query" key={`${index}-${query.shape}`}>
              <code>{query.shape}</code>
              <small>
                {query.level_label} · {query.occurrence_count} use{query.occurrence_count === 1 ? "" : "s"}
                {query.lines_written ? ` · ${query.lines_written.written} of ${query.lines_written.cited} lines written by ${recruiter ? "the candidate" : "you"}` : " · line authorship unknown"}
                {` · ${query.wired.detail}`}
                {query.test ? ` · targeted by ${query.test.path}${query.test.ci === "passed" ? " (CI passed on the latest commit)" : ""}` : " · no targeting test"}
                {" · correctness unverified"}
              </small>
              {query.evidence.map((item) => (
                <div key={item.url}>
                  <EvidenceLink item={item} />
                  {recruiter && item.copy_check && <small className="muted"> {copyCheckLabels[item.copy_check]}</small>}
                  {recruiter && item.advisory?.map((signal) => (
                    <small className="flag-warn" key={signal.id}> <AlertTriangle size={10} /> {signal.label}: {signal.detail}</small>
                  ))}
                </div>
              ))}
            </article>
          ))}
        </details>
      )}
      {result.skipped_total > 0 && (
        <details className="sql-queries">
          <summary>Not counted ({result.skipped_total})</summary>
          {result.skipped.map((item) => (
            <div className="sql-query" key={`${item.url}-${item.kind}`}>
              <EvidenceLink item={item} />
              <small className="muted">{item.reason}</small>
            </div>
          ))}
          {result.skipped_total > result.skipped.length && <small className="muted">{result.skipped_total - result.skipped.length} more not shown.</small>}
        </details>
      )}
      {result.notes.map((note) => <p className="sql-depth-small" key={note}>{note}</p>)}
      <p className="sql-depth-small">{result.weights_note} SQL depth never changes a skill level or the role match.</p>
    </details>
  );
}
