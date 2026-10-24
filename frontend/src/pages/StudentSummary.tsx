import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  Award,
  BookOpenCheck,
  CheckCircle2,
  ChevronDown,
  Code2,
  Database,
  Download,
  ExternalLink,
  FileCheck2,
  FolderGit2,
  Github,
  Layers,
  Printer,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  TrendingUp,
} from "lucide-react";
import { api, skillLabel, type Passport, type PassportSkill } from "../api";
import LadderPill from "../components/LadderPill";
import { sqlDepthHeadline } from "../sqlDepth";
import { growthUnavailable, GROWTH_UNAVAILABLE } from "../passportNotes";
import { showsNotCountedNote } from "../evidencePanel";

export default function StudentSummary() {
  const { id = "" } = useParams();
  const [passport, setPassport] = useState<Passport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const paperRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setLoading(true);
    setError("");
    api.passport(id)
      .then((data) => setPassport(data))
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false));
  }, [id]);

  const handlePrint = () => {
    const details = [...(paperRef.current?.querySelectorAll<HTMLDetailsElement>("details.summary-evidence-details") ?? [])];
    const previous = details.map((item) => item.open);
    details.forEach((item) => { item.open = true; });
    const restore = () => details.forEach((item, index) => { item.open = previous[index]; });
    window.addEventListener("afterprint", restore, { once: true });
    window.requestAnimationFrame(() => window.print());
  };

  const downloadBadge = async () => {
    try {
      const credential = await api.badge(id);
      const blob = new Blob([JSON.stringify(credential, null, 2)], { type: "application/ld+json" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `skill-passport-${id}.json`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setDownloadError(reason instanceof Error ? reason.message : "Could not download the credential.");
    }
  };

  if (loading) {
    return (
      <div className="summary-page-loading">
        <span className="spin"><RefreshCw size={24} /></span>
        <p>Loading student summary report…</p>
      </div>
    );
  }

  if (error || !passport) {
    return (
      <div className="summary-page-error">
        <div className="notice notice-error">{error || "Passport report not found."}</div>
        <Link className="button button-light" to={`/passport/${id}`}>
          <ArrowLeft size={15} /> Back to Passport
        </Link>
      </div>
    );
  }

  // Skills grouped by level
  const demonstratedSkills = passport.skills.filter((s) => s.level === "Demonstrated");
  const appliedSkills = passport.skills.filter((s) => s.level === "Applied");
  const usedSkills = passport.skills.filter((s) => s.level === "Used");
  const claimedSkills = passport.skills.filter(
    (s) => s.level === "Claimed" || (!s.evidence || s.evidence.length === 0),
  );

  const evidencedCount = demonstratedSkills.length + appliedSkills.length + usedSkills.length;
  const codeBackedCount = demonstratedSkills.length + appliedSkills.length;
  const certifiedSkills = passport.skills.filter((s) => s.certified);

  // Synthesize top demonstrated strengths narrative
  const topEvidencedNames = [...demonstratedSkills, ...appliedSkills]
    .slice(0, 5)
    .map((s) => skillLabel(s));

  const strengthsOverview = topEvidencedNames.length > 0
    ? `Observed evidence is available for ${evidencedCount} technical skills, including code-backed evidence for ${codeBackedCount}; stronger sampled examples include ${topEvidencedNames.join(", ")}. Levels describe evidence of use in the analysed sample, not overall ability.`
    : `Candidate profile has been analysed across GitHub repositories and documents. Detailed evidence breakdown is listed below.`;

  const reportDateFormatted = new Date(passport.created_at).toLocaleDateString("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  return (
    <div className="summary-report-container">
      {/* Action bar (hidden in print) */}
      <div className="summary-action-bar no-print">
        <Link className="summary-back-link" to={`/passport/${id}`}>
          <ArrowLeft size={16} />
          <span>Back to Live Passport</span>
        </Link>
        <div className="summary-action-buttons">
          <button type="button" className="button button-light" onClick={downloadBadge}>
            <Download size={16} />
            <span>Download credential JSON</span>
          </button>
          <button
            type="button"
            className="button summary-print-button"
            onClick={handlePrint}
            id="download-summary-pdf-btn"
          >
            <Printer size={16} />
            <span>Download PDF / Print</span>
          </button>
        </div>
      </div>

      <div className="summary-paper" ref={paperRef}>
        {/* 1. Header */}
        <header className="summary-header">
          <div className="summary-header-top">
            <div className="summary-brand">
              <span className="summary-brand-icon"><ShieldCheck size={22} /></span>
              <div>
                <span className="summary-brand-title">SKILL PASSPORT</span>
                <span className="summary-brand-tagline">DETERMINISTIC EVIDENCE REPORT</span>
              </div>
            </div>
            <div className="summary-header-meta">
              <div className="summary-meta-badge">
                <span className="summary-meta-label">REPORT DATE</span>
                <span className="summary-meta-val">{reportDateFormatted}</span>
              </div>
              {passport.is_demo && <span className="demo-badge">DEMO DATA</span>}
            </div>
          </div>

          <div className="summary-candidate-banner">
            <div className="summary-candidate-primary">
              <span className="eyebrow">VERIFIED CANDIDATE PROFILE</span>
              <h1 className="summary-candidate-name">
                <a
                  href={`https://github.com/${passport.github_username}`}
                  target="_blank"
                  rel="noreferrer"
                  className="summary-github-link"
                >
                  <Github size={24} />
                  <span>@{passport.github_username}</span>
                </a>
              </h1>
              <div className="summary-role-pill">
                <strong>Target Role:</strong> {passport.target_role}
              </div>
            </div>
            <div className="summary-passport-id-box">
              <span className="summary-id-label">PASSPORT ID</span>
              <code className="summary-id-val">{passport.id}</code>
            </div>
          </div>
        </header>

        {/* 2. At-a-glance summary */}
        <section className="summary-section">
          <div className="summary-section-heading">
            <Sparkles size={18} className="summary-heading-icon" />
            <h2>At-a-Glance Summary</h2>
          </div>

          <div className="summary-metrics-grid">
            <div className="summary-metric-card highlighted">
              <span className="summary-metric-num">{evidencedCount}</span>
              <span className="summary-metric-label">Evidenced Skills</span>
              <small>Demonstrated, Applied & Used</small>
            </div>
            <div className="summary-metric-card">
              <span className="summary-metric-num">{demonstratedSkills.length}</span>
              <span className="summary-metric-label">Demonstrated</span>
              <small>Applied & exercised in tests</small>
            </div>
            <div className="summary-metric-card">
              <span className="summary-metric-num">{appliedSkills.length}</span>
              <span className="summary-metric-label">Applied</span>
              <small>Directly committed source lines</small>
            </div>
            <div className="summary-metric-card">
              <span className="summary-metric-num">
                {passport.sources.certificates ?? 0}
              </span>
              <span className="summary-metric-label">Certificates</span>
              <small>Uploaded certificate documents</small>
            </div>
            <div className="summary-metric-card">
              <span className="summary-metric-num">
                {passport.sources.reports ?? passport.reports ? (Array.isArray(passport.reports) ? passport.reports.length : (passport.sources.reports || 0)) : 0}
              </span>
              <span className="summary-metric-label">Project Reports</span>
              <small>Cross-checked with code</small>
            </div>
            <div className="summary-metric-card">
              <span className="summary-metric-num">
                {passport.coverage?.repos_analysed ?? passport.sources.repos}
              </span>
              <span className="summary-metric-label">Repositories</span>
              <small>Analysed GitHub codebases</small>
            </div>
          </div>

          {downloadError && <div className="notice notice-error no-print">{downloadError}</div>}
          <div className="summary-overview-box">
            <p className="summary-overview-text">
              <strong>Demonstrated Strengths: </strong>
              {strengthsOverview}
            </p>
          </div>
        </section>

        {/* 3. Skills and Evidence */}
        <section className="summary-section">
          <div className="summary-section-heading">
            <Code2 size={18} className="summary-heading-icon" />
            <h2>Skills & Evidence</h2>
          </div>
          <p className="summary-section-sub">
            Skills categorized by deterministic verification level. Click any item on screen to expand cited code files, or review them directly in the printed PDF.
          </p>

          {/* Level 1: Demonstrated */}
          <div className="summary-skill-tier">
            <div className="summary-tier-header">
              <LadderPill level="Demonstrated" />
              <span className="summary-tier-title">Demonstrated ({demonstratedSkills.length})</span>
              <span className="summary-tier-desc">Applied in source code AND exercised by candidate-authored tests</span>
            </div>
            {demonstratedSkills.length > 0 ? (
              <div className="summary-skill-cards">
                {demonstratedSkills.map((skill) => (
                  <SkillEvidenceCard key={skill.skill_id} skill={skill} />
                ))}
              </div>
            ) : (
              <p className="summary-tier-empty">No skills met the Demonstrated criteria in the analysed files.</p>
            )}
          </div>

          {/* Level 2: Applied */}
          <div className="summary-skill-tier">
            <div className="summary-tier-header">
              <LadderPill level="Applied" />
              <span className="summary-tier-title">Applied ({appliedSkills.length})</span>
              <span className="summary-tier-desc">Directly used in source code the candidate committed to</span>
            </div>
            {appliedSkills.length > 0 ? (
              <div className="summary-skill-cards">
                {appliedSkills.map((skill) => (
                  <SkillEvidenceCard key={skill.skill_id} skill={skill} />
                ))}
              </div>
            ) : (
              <p className="summary-tier-empty">No skills at Applied level.</p>
            )}
          </div>

          {/* Level 3: Used */}
          <div className="summary-skill-tier">
            <div className="summary-tier-header">
              <LadderPill level="Used" />
              <span className="summary-tier-title">Used ({usedSkills.length})</span>
              <span className="summary-tier-desc">Configured in project manifests or dependencies authored by candidate</span>
            </div>
            {usedSkills.length > 0 ? (
              <div className="summary-skill-cards">
                {usedSkills.map((skill) => (
                  <SkillEvidenceCard key={skill.skill_id} skill={skill} />
                ))}
              </div>
            ) : (
              <p className="summary-tier-empty">No skills at Used level.</p>
            )}
          </div>

          {/* Level 4: Claimed / Unverified */}
          <div className="summary-skill-tier claimed-tier">
            <div className="summary-tier-header">
              <LadderPill level="Claimed" />
              <span className="summary-tier-title">Claimed / Unverified in Sample ({claimedSkills.length})</span>
              <span className="summary-tier-desc">Listed on resume or certificate, but no qualifying code found in sample</span>
            </div>
            <div className="summary-claimed-callout">
              <AlertCircle size={15} />
              <span>
                <strong>Unverified Notice:</strong> These skills were listed on the candidate’s resume or certificates, but no candidate-authored evidence was verified in the analysed source code sample. This indicates absence of evidence in the sampled files, not lack of ability.
              </span>
            </div>
            {claimedSkills.length > 0 ? (
              <div className="summary-claimed-tags">
                {claimedSkills.map((skill) => (
                  <span key={skill.skill_id} className="summary-claimed-tag">
                    {skillLabel(skill)}
                    {skill.certified && (
                      <span title="Skill mentioned in certificate text" className="certified-badge-wrap">
                        <FileCheck2 size={12} className="certified-icon" aria-label="Skill mentioned in certificate text" />
                      </span>
                    )}
                  </span>
                ))}
              </div>
            ) : (
              <p className="summary-tier-empty">All claimed skills were verified with code evidence.</p>
            )}
          </div>
        </section>

        {/* 4. Projects and Credentials */}
        <section className="summary-section">
          <div className="summary-section-heading">
            <Award size={18} className="summary-heading-icon" />
            <h2>Projects & Credentials</h2>
          </div>

          <div className="summary-grid-2col">
            {/* Credentials / Certificates */}
            <div className="summary-subcard">
              <div className="summary-subcard-title">
                <FileCheck2 size={16} />
                <span>Skills mentioned in certificates ({certifiedSkills.length})</span>
              </div>
              {certifiedSkills.length > 0 ? (
                <ul className="summary-credentials-list">
                  {certifiedSkills.map((skill) => (
                    <li key={skill.skill_id} className="summary-credential-item">
                      <CheckCircle2 size={14} className="credential-check" />
                      <div>
                        <strong>{skillLabel(skill)}</strong>
                        <span className="credential-meta">Skill name matched in uploaded certificate text; issuer and credential validity were not independently verified.</span>
                      </div>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="summary-empty-note">
                  {passport.sources.certificates > 0
                    ? "No skills were matched in uploaded certificate text."
                    : "No certificate documents were uploaded."}
                </p>
              )}
            </div>

            {/* Project Reports */}
            <div className="summary-subcard">
              <div className="summary-subcard-title">
                <FolderGit2 size={16} />
                <span>Project Reports ({Array.isArray(passport.reports) ? passport.reports.length : 0})</span>
              </div>
              {Array.isArray(passport.reports) && passport.reports.length > 0 ? (
                <div className="summary-reports-list">
                  {passport.reports.map((report, idx) => (
                    <div key={idx} className="summary-report-item">
                      <div className="summary-report-name">
                        <BookOpenCheck size={14} />
                        <strong>{report.filename}</strong>
                      </div>
                      <div className="summary-report-stats">
                        <span>{report.claims.filter((c) => c.status === "corroborated").length} claims corroborated with code</span>
                        {report.claims.filter((c) => c.status === "not_evidenced").length > 0 && (
                          <span className="muted"> · {report.claims.filter((c) => c.status === "not_evidenced").length} not evidenced in code</span>
                        )}
                      </div>
                      {report.repo_links && report.repo_links.length > 0 && (
                        <div className="summary-report-repos">
                          {report.repo_links.map((link, lidx) => (
                            <a
                              key={lidx}
                              href={link.url}
                              target="_blank"
                              rel="noreferrer"
                              className="summary-report-repo-link"
                            >
                              <Github size={11} />
                              <span>{link.owner}/{link.repo}</span>
                              <ExternalLink size={9} />
                            </a>
                          ))}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="summary-empty-note">
                  No separate project reports submitted. Technical analysis was derived directly from public GitHub repositories and resume.
                </p>
              )}
            </div>
          </div>
        </section>

        {/* 5. Skill Depth */}
        <section className="summary-section">
          <div className="summary-section-heading">
            <Layers size={18} className="summary-heading-icon" />
            <h2>Skill Depth & SQL Analysis</h2>
          </div>

          <div className="summary-depth-container">
            {/* SQL Depth */}
            {passport.sql_depth ? (
              <div className="summary-depth-card">
                <div className="summary-depth-header">
                  <div className="summary-depth-title">
                    <Database size={16} />
                    <span>SQL Evidence Depth</span>
                  </div>
                  <span className="summary-depth-badge">
                    {sqlDepthHeadline(passport.sql_depth)}
                  </span>
                </div>
                <p className="summary-depth-desc">
                  Structured query analysis evaluated {passport.sql_depth.distinct_query_count} distinct SQL statements in candidate code.
                  {passport.sql_depth.concepts_found.length > 0 ? "Observed SQL concepts include: " : "No SQL concepts were found in the analysed sample."}{" "}
                  {passport.sql_depth.concepts_found.length > 0
                    ? passport.sql_depth.concepts_found.map((c) => c.label).join(", ")
                    : null}
                </p>
                <p className="summary-depth-desc">{passport.sql_depth.note}</p>
                <ul className="summary-depth-citations">
                  {[...new Map(Object.values(passport.sql_depth.parts).flatMap((part) => part.evidence).map((item) => [item.url, item])).values()].map((item) => (
                    <li key={item.url}><a href={item.url} target="_blank" rel="noreferrer">{item.repo}/{item.file_path}:{item.start_line}–{item.end_line}</a></li>
                  ))}
                </ul>
                {passport.sql_depth.cap_reason && (
                  <div className="summary-depth-note">
                    <AlertCircle size={13} />
                    <span>{passport.sql_depth.cap_reason}</span>
                  </div>
                )}
              </div>
            ) : null}

            {/* General Skill Depth */}
            {passport.skill_depth && Object.keys(passport.skill_depth).length > 0 ? (
              <div className="summary-depth-card">
                <div className="summary-depth-header">
                  <div className="summary-depth-title">
                    <Code2 size={16} />
                    <span>Framework Competencies Depth</span>
                  </div>
                </div>
                <div className="summary-framework-depth-list">
                  {Object.entries(passport.skill_depth).map(([name, depth]) => {
                    if (!depth) return null;
                    const observed = depth.competencies.filter((c) => c.status === "observed");
                    return (
                      <div key={name} className="summary-depth-profile-item">
                        <div className="summary-profile-top">
                          <strong>{skillLabel({ skill_id: name })}</strong>
                          <span className="depth-score-tag">
                            {depth.score !== null ? `${depth.score}/100 · ${depth.label}` : depth.label}
                          </span>
                        </div>
                        <p className="depth-profile-obs">
                          {observed.length} observed competencies out of {depth.competencies.length} evaluated · {depth.coverage.percent}% coverage.
                        </p>
                        <p className="summary-depth-desc">{depth.disclaimer}</p>
                        <ul className="summary-depth-citations">
                          {depth.competencies.flatMap((competency) => competency.evidence.map((item) => ({ competency, item }))).map(({ competency, item }) => (
                            <li key={`${item.url}-${competency.id}`}><strong>{competency.label}:</strong> <a href={item.url} target="_blank" rel="noreferrer">{item.repo}/{item.file_path}:{item.start_line}–{item.end_line}</a></li>
                          ))}
                        </ul>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : null}

            {!passport.sql_depth && (!passport.skill_depth || Object.keys(passport.skill_depth).length === 0) && (
              <div className="summary-depth-card">
                <p className="summary-empty-note">
                  Skill depth pilot evaluation was not active or insufficient structured query instances were found in the analysed code sample. Overall skill categorization remains active above.
                </p>
              </div>
            )}
          </div>
        </section>

        {/* 6. Coverage and Context */}
        <section className="summary-section">
          <div className="summary-section-heading">
            <Github size={18} className="summary-heading-icon" />
            <h2>Coverage & Context</h2>
          </div>

          <div className="summary-coverage-box">
            <p className="summary-coverage-statement">
              <strong>Context & Sampling Notice:</strong> This analysis evaluates a representative sample of public repositories ({passport.coverage?.repos_analysed ?? passport.sources.repos} analysed) and source files ({passport.coverage?.source_files_read ?? 0} files read, ~{passport.coverage?.lines_read?.toLocaleString() ?? 0} lines). Findings describe strictly the verified material within this sample; absence of evidence does not imply that the candidate lacks the skill.
            </p>

            {passport.coverage?.repos && passport.coverage.repos.length > 0 && (
              <div className="summary-repo-table-wrap">
                <table className="summary-repo-table">
                  <thead>
                    <tr>
                      <th>Repository</th>
                      <th>Files Read</th>
                      <th>Total Files</th>
                      <th>Lines Sampled</th>
                    </tr>
                  </thead>
                  <tbody>
                    {passport.coverage.repos.map((repo, idx) => (
                      <tr key={idx}>
                        <td className="repo-name-col">
                          <strong>{repo.name}</strong>
                        </td>
                        <td>{repo.source_files_read}</td>
                        <td>{repo.source_files_total}</td>
                        <td>{repo.lines_read.toLocaleString()}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>

        {/* 7. Growth Areas */}
        <section className="summary-section">
          <div className="summary-section-heading">
            <TrendingUp size={18} className="summary-heading-icon" />
            <h2>Growth Areas & Next Steps</h2>
          </div>
          <p className="summary-section-sub">
            Concrete suggestions to turn unverified role requirements into evidenced proof by committing new work.
          </p>

          {passport.growth_tasks && passport.growth_tasks.length > 0 ? (
            <div className="summary-growth-list">
              {passport.growth_tasks.map((task, idx) => (
                <div key={idx} className="summary-growth-item">
                  <div className="summary-growth-head">
                    <span className="summary-growth-skill">{skillLabel({ skill_id: task.skill_id })}</span>
                    <span className="summary-growth-target">Target Repo: <strong>{task.target_repo}</strong></span>
                  </div>
                  <p className="summary-growth-task">{task.task}</p>
                  <div className="summary-growth-proof">
                    <strong>Evidence it will create:</strong> {task.evidence_it_will_create}
                  </div>
                </div>
              ))}
            </div>
          ) : growthUnavailable(passport) ? (
            <div className="summary-empty-note">{GROWTH_UNAVAILABLE}</div>
          ) : (
            <div className="summary-empty-note">
              No immediate role gaps were flagged for the target role: <strong>{passport.target_role}</strong>. Continue building and re-check when publishing new repositories.
            </div>
          )}
        </section>

        {/* Footer */}
        <footer className="summary-footer">
          <p>
            Generated by <strong>Skill Passport</strong> · Deterministic proof platform.
            Levels reflect candidate code contributions and commit histories.
          </p>
        </footer>
      </div>
    </div>
  );
}

/** Individual Skill Card with expandable evidence on screen, auto-expanded on PDF print */
function SkillEvidenceCard({ skill }: { skill: PassportSkill }) {
  const displayName = skillLabel(skill);

  return (
    <div className="summary-skill-card">
      <div className="summary-skill-card-head">
        <div className="summary-skill-name-row">
          <span className="summary-skill-name">{displayName}</span>
          {skill.certified && (
            <span className="summary-cert-badge" title="Skill mentioned in certificate text">
              <FileCheck2 size={12} /> Certificate mention
            </span>
          )}
        </div>
        <span className="summary-evidence-count">
          {skill.evidence.length} evidence citation{skill.evidence.length === 1 ? "" : "s"}
        </span>
      </div>

      {skill.evidence.length > 0 && (
        <details className="summary-evidence-details">
          <summary className="summary-evidence-toggle">
            <span>View code citations ({skill.evidence.length})</span>
            <ChevronDown size={14} className="details-chevron" />
          </summary>
          <div className="summary-evidence-list">
            {skill.evidence.map((ev, idx) => (
              <div key={idx} className="summary-evidence-citation">
                <div className="citation-loc">
                  <strong>{ev.repo}</strong> · <code>{ev.file_path}:{ev.start_line}-{ev.end_line}</code>
                </div>
                {ev.url && (
                  <a
                    href={ev.url}
                    target="_blank"
                    rel="noreferrer"
                    className="citation-link"
                  >
                    <span>Inspect GitHub lines</span>
                    <ExternalLink size={10} />
                  </a>
                )}
                {showsNotCountedNote(ev) && (
                  <p className="not-counted-note">Found in a file you did not commit to; not counted toward the skill level.</p>
                )}
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  );
}
