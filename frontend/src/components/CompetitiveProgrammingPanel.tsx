import {
  AlertTriangle, CalendarClock, CheckCircle2, Code2, Flag, Flame, History,
  ShieldOff, Tags, Trophy, UserX,
} from "lucide-react";
import type { CompetitiveProgramming } from "../api";

function fmtDate(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? null : date.toLocaleDateString();
}

/**
 * Official Codeforces rank bands. Used only to colour the rank chip and place
 * the rating marker inside its band - never to compute or invent stats.
 */
const RANKS: { name: string; min: number; color: string }[] = [
  { name: "newbie", min: 0, color: "#9aa0a6" },
  { name: "pupil", min: 1200, color: "#7ce7b4" },
  { name: "specialist", min: 1400, color: "#4fc3f7" },
  { name: "expert", min: 1600, color: "#7c9aff" },
  { name: "candidate master", min: 1900, color: "#c792ff" },
  { name: "master", min: 2100, color: "#ffcb6b" },
  { name: "international master", min: 2300, color: "#ff9e64" },
  { name: "grandmaster", min: 2400, color: "#ff6b6b" },
  { name: "international grandmaster", min: 2600, color: "#ff5370" },
  { name: "legendary grandmaster", min: 3000, color: "#ff5370" },
];
const BAND_TOP = 3400; // display ceiling for the open legendary band

function rankIndex(rank: string | null | undefined, rating: number | null | undefined): number {
  const byName = RANKS.findIndex((r) => r.name === (rank ?? "").toLowerCase());
  if (byName >= 0) return byName;
  const value = rating ?? 0;
  let index = 0;
  RANKS.forEach((r, i) => { if (value >= r.min) index = i; });
  return index;
}

function capitalize(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

/**
 * Slim proportion bar with an icon label. The three labelled entries get distinct
 * segment colours with matching legend dots; every further segment is one neutral
 * grey read as "others", which the "+n" legend entry covers. The full breakdown
 * also appears in the panel's About disclosure for tap/keyboard access.
 */
function ProportionBar({ icon: Icon, hint, entries }: { icon: typeof Tags; hint: string; entries: [string, number][] }) {
  const total = entries.reduce((sum, [, count]) => sum + count, 0) || 1;
  const full = entries.map(([name, count]) => `${name} ${count}`).join(", ");
  const shown = entries.slice(0, 3);
  return (
    <div className="cp-bar-block" title={`${hint}: ${full}`} aria-label={`${hint}: ${full}`}>
      <Icon size={13} aria-hidden />
      <span className="cp-bar" role="img" aria-hidden>
        {entries.map(([name], index) => (
          <i key={name} className={`cp-seg ${index < 3 ? `cp-seg-${index}` : "cp-seg-x"}`} style={{ width: `${(entries[index][1] / total) * 100}%` }} />
        ))}
      </span>
      <span className="cp-bar-legend">
        {shown.flatMap(([name, count], index) => [
          index > 0 ? " · " : null,
          <span key={name}><i className={`cp-key cp-seg-${index}`} />{name} {count}</span>,
        ])}
        {entries.length > shown.length ? ` · +${entries.length - shown.length}` : ""}
      </span>
    </div>
  );
}

/**
 * Codeforces section, a separate competitive-programming axis that never feeds
 * skill levels, role match or coverage. Renders nothing for passports built
 * before the feature (undefined) or built without a handle (null). Failure
 * payloads show the error honestly; retained data after a failed refresh is
 * marked as stale. Sampled stats are labelled with the actual examined count.
 * The truthful qualifiers stay as icon marks whose tooltips carry the full text.
 */
export default function CompetitiveProgrammingPanel({ result }: {
  result: CompetitiveProgramming | null | undefined;
}) {
  if (result == null) return null;

  const asOf = fmtDate(result.fetched_at);
  const examined = result.submissions_examined ?? result.submission_sample;
  const rated = !result.error && result.profile && result.profile.rating != null;
  const rankIdx = rankIndex(result.profile?.rank, result.profile?.rating);
  const rank = RANKS[rankIdx];
  const bandCeil = RANKS[rankIdx + 1]?.min ?? BAND_TOP;
  const bandPct = rated
    ? Math.min(100, Math.max(0, ((result.profile!.rating! - rank.min) / (bandCeil - rank.min)) * 100))
    : 0;
  const maxRank = result.profile?.max_rating != null ? RANKS[rankIndex(result.profile?.max_rank, result.profile?.max_rating)] : null;

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
            <p className="unavailable-note"><History size={11} /> Refresh failed; showing retained data{asOf ? ` from ${asOf}` : ""}.</p>
          )}
          <div className="cp-rating">
            <b>{rated ? result.profile!.rating : "Unrated"}</b>
            <span className="cp-rank" style={{ color: rated ? rank.color : undefined }}>
              <i className="cp-dot" style={{ background: rated ? rank.color : "var(--muted)" }} />
              {rated ? capitalize(rank.name) : "No rated contests yet"}
            </span>
          </div>
          {rated && (
            <div
              className="cp-meter-block"
              role="img"
              aria-label={`Rating ${result.profile!.rating} within the ${rank.name} band, ${rank.min} to ${bandCeil}${result.profile!.max_rating != null ? `; best rating ${result.profile!.max_rating}` : ""}`}
            >
              <div className="cp-meter" title={`${capitalize(rank.name)} band ${rank.min}-${bandCeil}`}>
                <i className="cp-marker" style={{ left: `calc(${bandPct}% - 5px)`, background: rank.color }} />
              </div>
              <div className="cp-bounds">
                <span>{rank.min}</span>
                {result.profile!.max_rating != null && (
                  <span
                    className="cp-best"
                    title={`Best (max) rating ${result.profile!.max_rating}${result.profile!.max_rank ? ` - ${capitalize(result.profile!.max_rank)} band` : ""}`}
                  >
                    <i className="cp-diamond" style={{ background: maxRank!.color }} />best {result.profile!.max_rating}
                  </span>
                )}
                <span>{bandCeil}</span>
              </div>
            </div>
          )}

          <div className="cp-stats">
            {(result.rated_contests ?? 0) > 0 && (
              <div className="cp-stat" title={`${result.rated_contests} rated contests${fmtDate(result.last_rated_contest_at) ? ` · last ${fmtDate(result.last_rated_contest_at)}` : ""}`}>
                <b><Flag size={13} />{result.rated_contests}</b><span>contests</span>
              </div>
            )}
            <div className="cp-stat" title={`${result.solved_count ?? 0} problems solved${examined ? ` · last ${examined} submissions examined` : ""}`}>
              <b><CheckCircle2 size={13} />{result.solved_count ?? 0}</b><span>solved</span>
            </div>
            {result.solved_max_problem_rating != null && (
              <div className="cp-stat" title={`Hardest solved problem rated ${result.solved_max_problem_rating}`}>
                <b><Flame size={13} />{result.solved_max_problem_rating}</b><span>hardest</span>
              </div>
            )}
          </div>

          {result.top_tags && result.top_tags.length > 0 && (
            <ProportionBar icon={Tags} hint="Top topics" entries={result.top_tags.slice(0, 5)} />
          )}
          {result.languages && result.languages.length > 0 && (
            <ProportionBar icon={Code2} hint="Languages" entries={result.languages.slice(0, 3)} />
          )}
        </>
      )}

      <p className="cp-marks">
        <span title="Separate record - never affects skill levels, role match or coverage"><ShieldOff size={11} />separate axis</span>
        <span title="Handle is not verified as the student's"><UserX size={11} />unverified</span>
        {asOf && <span title={`Data as of ${asOf}`}><CalendarClock size={11} />{asOf}</span>}
      </p>

      <details className="cp-about">
        <summary>About this section</summary>
        <ul>
          <li>Separate record - never affects skill levels, role match or coverage.</li>
          <li>The handle is not verified as the student's.</li>
          {examined ? <li>Solved and hardest counts come from the last {examined} submissions examined.</li> : null}
          {result.top_tags && result.top_tags.length > 0 && (
            <li>Top topics: {result.top_tags.slice(0, 5).map(([n, c]) => `${n} ${c}`).join(", ")}.</li>
          )}
          {result.languages && result.languages.length > 0 && (
            <li>Languages: {result.languages.slice(0, 3).map(([n, c]) => `${n} ${c}`).join(", ")}.</li>
          )}
        </ul>
      </details>
    </section>
  );
}
