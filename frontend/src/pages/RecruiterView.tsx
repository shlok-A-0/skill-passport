import { AlertTriangle, CheckCircle2, Clipboard, ExternalLink, FileSearch, ShieldCheck, XCircle } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Passport } from "../api";
import LadderPill from "../components/LadderPill";
import { verifiability } from "../verifiability";
import { skillName } from "../skillNames";

export default function RecruiterView() {
  const { id = "" } = useParams();
  const [passport, setPassport] = useState<Passport | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    api.passport(id).then(setPassport).catch((reason: Error) => setError(reason.message));
  }, [id]);

  async function copyLink() {
    try {
      await navigator.clipboard.writeText(window.location.href);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setError("Could not copy the share link. Copy the URL from your address bar.");
    }
  }

  if (error && !passport) return <div className="page-message notice-error">{error}</div>;
  if (!passport) return <div className="page-message">Loading recruiter summary…</div>;

  const topSkills = passport.skills
    .filter((skill) => skill.level !== "Claimed" && skill.evidence.some((item) => item.type === "code"))
    .slice(0, 5);
  const claimedSkills = passport.skills.filter((skill) => skill.level === "Claimed" || !skill.evidence.length);
  const interviews = passport.interview_kit;
  const verify = verifiability(passport);

  return (
    <div className="recruiter-page">
      <header className="recruiter-heading">
        <div>
          <div className="eyebrow">RECRUITER VIEW · EVIDENCE-BACKED SUMMARY</div>
          <h1>{passport.github_username}{passport.is_demo && <span className="demo-badge">DEMO DATA</span>}</h1>
          <p>Candidate for <strong>{passport.target_role}</strong> · {passport.sources.repos} GitHub repositories reviewed · <span className={`verify-pill verify-${verify.tier.replace(" ", "-").toLowerCase()}`} title={verify.warnings.map(({ signal, where }) => `${signal.label} (${where}): ${signal.detail}`).join("\n") || undefined}>Verifiability: {verify.tier === "Not checked" ? "not checked" : `${verify.tier} · ${verify.passed} of ${verify.checked} checks`}</span>{verify.copiedExcluded > 0 && <span className="copied-excluded"><AlertTriangle size={11} /> {verify.copiedExcluded} copied citation{verify.copiedExcluded === 1 ? "" : "s"} excluded</span>}</p>
        </div>
        <div className="recruiter-actions">
          <div className="match-total"><strong>{passport.role_match.met} <i>of</i> {passport.role_match.total}</strong><span>requirements met</span></div>
          <button className="button button-light" onClick={copyLink}><Clipboard size={14} /> {copied ? "Link copied" : "Copy share link"}</button>
        </div>
      </header>
      {error && <div className="notice notice-error">{error}</div>}
      <div className="recruiter-grid">
        <section className="panel requirement-panel">
          <div className="panel-heading"><div><div className="eyebrow">EXPLAINABLE ROLE MATCH</div><h2>Requirement checklist</h2></div><span className="muted">Must-have gaps first</span></div>
          <div className="requirement-list">
            {passport.role_match.requirements.map((requirement) => {
              const skill = passport.skills.find((item) => item.skill_id === requirement.skill_id);
              const Icon = requirement.status === "met" ? CheckCircle2 : XCircle;
              return (
                <article className={`requirement-row req-${requirement.status}`} key={requirement.skill_id}>
                  <Icon size={18} />
                  <div className="req-main">
                    <div className="req-title">{skillName(requirement.skill_id)} <span className={`importance importance-${requirement.importance}`}>{requirement.importance}</span></div>
                    <p>{requirement.reason}</p>
                  </div>
                  {skill && <LadderPill level={skill.level} />}
                  <Link className="view-evidence-link" to={`/passport/${passport.id}?skill=${requirement.skill_id}`} aria-label={`View ${requirement.skill_id} evidence`}><FileSearch size={15} /> Evidence</Link>
                </article>
              );
            })}
          </div>
        </section>
        <aside className="recruiter-side">
          <section className="panel verified-summary">
            <div className="panel-heading"><div><div className="eyebrow">CODE-BACKED SKILLS</div><h2>Verified strengths</h2></div><ShieldCheck size={19} color="#2fb36b" /></div>
            {topSkills.length ? topSkills.map((skill) => {
              const evidence = skill.evidence.find((item) => item.type === "code");
              const flags = skill.evidence.flatMap((item) => item.integrity ?? []).filter((signal) => signal.status === "warn").length;
              return (
                <Link className="strength-row" to={`/passport/${passport.id}?skill=${skill.skill_id}`} key={skill.skill_id}>
                  <span><strong>{skillName(skill.skill_id)}{flags > 0 && <span className="integrity-flag" title="Authorship signals need a human look"><AlertTriangle size={11} /> {flags} to check</span>}</strong><small>{evidence?.repo}/{evidence?.file_path} · L{evidence?.start_line}–L{evidence?.end_line}</small></span>
                  <LadderPill level={skill.level} />
                </Link>
              );
            }) : <p className="muted">No authored code skills have been verified yet.</p>}
          </section>
          <section className="panel claimed-summary">
            <div className="panel-heading"><div><div className="eyebrow">NOT INCLUDED IN MATCH</div><h2>Claimed, unverified</h2></div><span className="count-tag">{claimedSkills.length}</span></div>
            {claimedSkills.length ? (
              <>
                <div className="claimed-inline">{claimedSkills.slice(0, 5).map((skill) => <span key={skill.skill_id}>{skillName(skill.skill_id)}</span>)}</div>
                <p>Listed on resume. No evidence found in submitted work.</p>
              </>
            ) : <p>No resume-only claims in this passport.</p>}
          </section>
          <details className="panel interview-summary">
            <summary><span><div className="eyebrow">DEFEND-YOUR-WORK KIT</div><strong>Interview questions</strong></span><span className="count-tag">{interviews.length} skills</span></summary>
            {interviews.length ? interviews.slice(0, 5).map((entry) => (
              <article className="interview-entry" key={entry.skill_id}>
                <strong>{skillName(entry.skill_id)}</strong>
                {entry.questions.map((question, index) => (
                  <div className="question" key={question.q}><p>{index + 1}. {question.q}</p><small>Good answer mentions: {question.good_answer_mentions}</small></div>
                ))}
              </article>
            )) : <p className="muted">Interview prompts will appear when verified code evidence is available.</p>}
          </details>
        </aside>
      </div>
      <footer className="recruiter-disclaimer"><ShieldCheck size={14} /> Applied and Demonstrated skills link to code the candidate authored. Used reflects dependencies or configuration; unverified claims are never counted. <Link to={`/passport/${passport.id}`}><ExternalLink size={13} /> Full passport</Link></footer>
    </div>
  );
}
