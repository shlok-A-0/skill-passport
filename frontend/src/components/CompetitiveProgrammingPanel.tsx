import { AlertTriangle, Trophy } from "lucide-react";
import type { CompetitiveProgramming } from "../api";

function fmtDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString();
}

function capitalize(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

/** Slim proportion bar with a plain-text legend; counts stay visible, no badges. */
function ProportionBar({ label, entries }: { label: string; entries: [string, number][] }) {
  const total = entries.reduce((sum, [, count]) => sum + count, 0) || 1;
  return (
    <div className="cp-bar-block">
      <span className="cp-bar-label">{label}</span>
      <span className="cp-bar" role="img" aria-label={entries.map(([name, count]) => `${name} ${count}`).join(", ")}>
        {entries.map(([name, count], index) => (
          <i key={name} className={`cp-seg cp-seg-${index}`} style={{ width: `${(count / total) * 100}%` }} />
        ))}
      </span>
      <span className="cp-bar-legend">{entries.map(([name, count]) => `${name} ${count}`).join(" · ")}</span>
    </div>
  );
}

/**
 * Codeforces section, a separate competitive-programming axis that never feeds
 * skill levels, role match or coverage. Renders nothing for passports built
 * before the feature (undefined) or built without a handle (null). Failure
 * payloads show the error honestly; retained data after a failed refresh is
 * badged as stale. Sampled stats are labelled with the actual examined count.
 */
export default function CompetitiveProgrammingPanel({ result }: {
  result: CompetitiveProgramming | null | undefined;
}) {
  if (result == null) return null;

  const asOf = fmtDate(result.fetched_at);
  // Label sampled stats with the number actually examined, never the requested cap.
  const examined = result.submissions_examined ?? result.submission_sample;
  const rated = !result.error && result.profile && result.profile.rating != null;

  return (
    <section className="panel cp-panel">
      <div className="panel-heading">
        <div><div className="eyebrow">COMPETITIVE PROGRAMMING</div><h2><Trophy size={16} /> {result.handle}</h2></div>
      </div>

      {result.error === "not_found" && (
        <p className="unavailable-note"><AlertTriangle size={11} /> No public Codeforces account named “{result.handle}” was found.</p>
      )}
      {result.error === "unavailable" && (
        <p className="unavailable-note"><AlertTriangle size={11} /> Codeforces could not be reached during the last check. No data was recorded.</p>
      )}

      {!result.error && result.profile && (
        <>
          {result.stale && (
            <p className="unavailable-note"><AlertTriangle size={11} /> Refresh failed; showing retained data{asOf ? ` from ${asOf}` : ""}.</p>
          )}
          <div className="cp-rating">
            <b>{rated ? result.profile!.rating : "Unrated"}</b>
            <span>
              {rated && result.profile!.rank ? capitalize(result.profile!.rank) : rated ? "Rated" : "No rated contests yet"}
              {result.profile.max_rating != null ? ` · best ${result.profile.max_rating}` : ""}
            </span>
          </div>
          <div className="cp-facts">
            {(result.rated_contests ?? 0) > 0 && (
              <div className="snapshot-row"><span>Rated contests</span><b>{result.rated_contests}{fmtDate(result.last_rated_contest_at) ? ` · last ${fmtDate(result.last_rated_contest_at)}` : ""}</b></div>
            )}
            <div className="snapshot-row">
              <span>Problems solved{examined ? ` · last ${examined} submissions` : ""}</span>
              <b>{result.solved_count ?? 0}{result.solved_max_problem_rating ? ` · hardest ${result.solved_max_problem_rating}` : ""}</b>
            </div>
          </div>
          {result.top_tags && result.top_tags.length > 0 && (
            <ProportionBar label="Top topics" entries={result.top_tags.slice(0, 5)} />
          )}
          {result.languages && result.languages.length > 0 && (
            <ProportionBar label="Languages" entries={result.languages.slice(0, 3)} />
          )}
        </>
      )}
      <p className="muted cp-meta">Separate record, never affects skill levels · handle not verified as the student's{asOf ? ` · data as of ${asOf}` : ""}</p>
    </section>
  );
}
