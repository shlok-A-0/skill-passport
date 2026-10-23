import { AlertTriangle, CheckCircle2, ExternalLink, Globe } from "lucide-react";
import { skillLabel, type Portfolio, type ProjectReport } from "../api";
import type { Audience } from "./RejectedClaims";

/**
 * Project reports and the portfolio link. Report claims are checked against counted evidence and
 * never raise a level; links to another account's repositories are shown to recruiters only.
 */
export default function ReportsPanel({ reports = [], portfolio, audience }: {
  reports?: ProjectReport[];
  portfolio?: Portfolio;
  audience: Audience;
}) {
  if (!reports.length && !portfolio) return null;
  const recruiter = audience === "recruiter";
  const claimCount = reports.reduce((total, report) => total + report.claims.length, 0);
  return (
    <details className="panel interview-summary reports-summary" open={!recruiter}>
      <summary>
        <span><div className="eyebrow">CLAIMS, NOT EVIDENCE</div><strong>Project reports &amp; portfolio</strong></span>
        <span className="count-tag">{claimCount} claim{claimCount === 1 ? "" : "s"}</span>
      </summary>
      <p className="muted">Reports are checked against counted evidence and never raise a level. Skills a report mentions without counted evidence stay unverified.</p>
      {reports.map((report, index) => (
        <article className="report-entry" key={`${report.filename}-${index}`}>
          <strong>{report.filename}</strong>
          {report.error && <p className="unavailable-note">{report.error}</p>}
          {report.claims.length > 0 && (
            <ul className="report-claims">
              {report.claims.map((claim) => (
                <li key={claim.skill_id}>
                  <span className="report-claim-skill">{skillLabel(claim)}</span>
                  {claim.status === "corroborated" ? (
                    claim.evidence?.url
                      ? <a className="flag-ok" href={claim.evidence.url} target="_blank" rel="noreferrer"><CheckCircle2 size={11} /> Corroborated by counted evidence <ExternalLink size={10} /></a>
                      : <span className="flag-ok"><CheckCircle2 size={11} /> Corroborated by counted evidence</span>
                  ) : (
                    <span className="muted">{recruiter ? "Not corroborated by counted evidence" : "Not yet corroborated by counted evidence"}</span>
                  )}
                  <small className="muted">“{claim.quote}”</small>
                </li>
              ))}
            </ul>
          )}
          {!report.error && !report.claims.length && <p className="muted">No framework skills were found in this report.</p>}
          {report.repo_links.length > 0 && (
            <ul className="report-links">
              {report.repo_links.map((link) => (
                <li key={link.url}>
                  <a href={link.url} target="_blank" rel="noreferrer">{link.owner}/{link.repo} <ExternalLink size={10} /></a>
                  {link.status === "own"
                    ? <span className="muted">{recruiter ? "own repository" : "your repository"}</span>
                    : recruiter && <span className="flag-warn"><AlertTriangle size={11} /> repository on another account</span>}
                </li>
              ))}
            </ul>
          )}
        </article>
      ))}
      {portfolio && (
        <p className="report-portfolio">
          <Globe size={12} /> <a href={portfolio.url} target="_blank" rel="noreferrer noopener">{portfolio.url}</a>{" "}
          <span className="muted">
            {portfolio.status === "own_github_pages"
              ? (recruiter ? "Hosted on this GitHub account" : "Hosted on your GitHub account")
              : "External site, ownership not verified"}
          </span>
        </p>
      )}
    </details>
  );
}
