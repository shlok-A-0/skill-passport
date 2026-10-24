import { AlertTriangle, CheckCircle2, ChevronDown, Clipboard, ExternalLink, FileSearch, MessagesSquare, ShieldCheck, XCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, NOT_FOUND_TOOLTIP, skillLabel, type Passport, type PassportSkill, type RequirementPacket } from "../api";
import CoverageSummary from "../components/CoverageSummary";
import IdentityChecks from "../components/IdentityChecks";
import InterviewWorkspace from "../components/InterviewWorkspace";
import LadderPill from "../components/LadderPill";
import ReportsPanel from "../components/ReportsPanel";
import SqlDepthPanel from "../components/SqlDepthPanel";
import SkillDepthPanel from "../components/SkillDepthPanel";
import { GROWTH_UNAVAILABLE, growthUnavailable, KIT_UNAVAILABLE, kitUnavailable } from "../passportNotes";
import { earlierSourcesText, notWrittenText, verifiability } from "../verifiability";
import { skillName } from "../skillNames";

const levelRank: Record<PassportSkill["level"], number> = { Claimed: 0, Used: 1, Applied: 2, Demonstrated: 3 };

/** The four questions for one requirement: support, attribution, uncertainty, what to ask next. */
function PacketDetails({ packet, reason }: { packet: RequirementPacket; reason: string }) {
  return (
    <div className="packet">
      <div>
        <h3>What supports it</h3>
        <p><strong>{packet.status_label}.</strong> {reason}</p>
        {packet.support.map((item) => (
          <a key={item.url} href={item.url} target="_blank" rel="noreferrer">
            {item.repo}/{item.file_path}{item.start_line > 0 ? ` · L${item.start_line}–L${item.end_line}` : ""} ({item.type}) <ExternalLink size={10} />
          </a>
        ))}
      </div>
      <div>
        <h3>Attribution</h3>
        <p><strong>{packet.attribution}</strong>{packet.attribution === "Not applicable" ? " · nothing was found to attribute." : " · how clearly the cited work traces to the candidate."}</p>
      </div>
      <div>
        <h3>What's uncertain</h3>
        {packet.uncertain.length ? <ul>{packet.uncertain.map((line) => <li key={line}>{line}</li>)}</ul> : <p className="muted">Nothing flagged.</p>}
      </div>
      <div>
        <h3>What to ask next</h3>
        <p>{packet.ask_next.question}</p>
        {packet.ask_next.good_answer_mentions && <small>A good answer mentions: {packet.ask_next.good_answer_mentions}</small>}
      </div>
    </div>
  );
}

export default function RecruiterView() {
  const { id = "" } = useParams();
  const [passport, setPassport] = useState<Passport | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const loadVersion = useRef(0);

  useEffect(() => {
    const version = ++loadVersion.current;
    setPassport(null);
    setError("");
    api.reviewPassport(id)
      .then((data) => { if (version === loadVersion.current) setPassport(data); })
      .catch((reason: Error) => { if (version === loadVersion.current) setError(reason.message); });
    return () => { loadVersion.current += 1; };
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

  if (error && (!passport || passport.id !== id)) return <div className="page-message notice-error">{error}</div>;
  if (!passport || passport.id !== id) return <div className="page-message">Loading recruiter summary…</div>;

  // Strongest first: Demonstrated > Applied > Used, then the most evidence.
  const topSkills = passport.skills
    .filter((skill) => skill.level !== "Claimed" && skill.evidence.length > 0)
    .sort((left, right) => levelRank[right.level] - levelRank[left.level] || right.evidence.length - left.evidence.length)
    .slice(0, 5);
  const claimedSkills = passport.skills.filter((skill) => skill.level === "Claimed" || !skill.evidence.length);
  const interviews = passport.interview_kit;
  const verify = verifiability(passport);
  const openRequests = (passport.review_requests ?? []).filter((request) => request.status === "open").length;

  return (
    <div className="recruiter-page">
      <header className="recruiter-heading">
        <div>
          <div className="eyebrow">RECRUITER VIEW · EVIDENCE-BACKED SUMMARY</div>
          <h1>{passport.github_username}{passport.is_demo && <span className="demo-badge">DEMO DATA</span>}</h1>
          <p>Candidate for <strong>{passport.target_role}</strong> · {passport.sources.repos} GitHub repositories reviewed · <span className={`verify-pill verify-${verify.tier.replace(" ", "-").toLowerCase()}`} title={verify.warnings.map(({ signal, where }) => `${signal.label} (${where}): ${signal.detail}`).join("\n") || undefined}>Verifiability: {verify.tier === "Not checked" ? "not checked" : `${verify.tier} · ${verify.passed} of ${verify.checked} checks`}</span>{verify.earlierSources > 0 && <span className="earlier-sources"><AlertTriangle size={11} /> {earlierSourcesText(verify.earlierSources)}</span>}{verify.notWritten > 0 && <span className="earlier-sources"><AlertTriangle size={11} /> {notWrittenText(verify.notWritten)}</span>}{openRequests > 0 && <Link className="open-requests" to={`/recruiter/${passport.id}/evidence`}>{openRequests} open review request{openRequests === 1 ? "" : "s"}</Link>}</p>
          {passport.custom_role && <p className="custom-role-note">Custom role from job description: <strong>{passport.custom_role.title}</strong></p>}
          <CoverageSummary coverage={passport.coverage} />
          <IdentityChecks signals={passport.identity} />
          {growthUnavailable(passport) && <p className="unavailable-note">{GROWTH_UNAVAILABLE}</p>}
        </div>
        <div className="recruiter-actions">
          <div className="match-total"><strong>{passport.role_match.met} <i>of</i> {passport.role_match.total}</strong><span>requirements met</span></div>
          <a className="button" href="#interview"><MessagesSquare size={14} /> Interview kit</a>
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
              const packet = passport.requirement_packets?.find((item) => item.skill_id === requirement.skill_id);
              const Icon = requirement.status === "met" ? CheckCircle2 : XCircle;
              const open = expanded === requirement.skill_id;
              const quote = passport.custom_role?.requirements.find((item) => item.skill_id === requirement.skill_id)?.quote;
              return (
                <div className={`requirement-item ${open ? "requirement-open" : ""}`} key={requirement.skill_id}>
                  <article className={`requirement-row req-${requirement.status}`}>
                    <Icon size={18} />
                    <button type="button" className="req-main" onClick={() => setExpanded(open ? null : requirement.skill_id)} aria-expanded={open}>
                      <div className="req-title">
                        {skillLabel(requirement)} <span className={`importance importance-${requirement.importance}`}>{requirement.importance}</span>
                        {requirement.status === "missing"
                          ? <span className="status-not-found" title={NOT_FOUND_TOOLTIP}>Not found</span>
                          : <span className={`status-pill status-${requirement.status}`}>{packet?.status_label ?? requirement.status}</span>}
                        {packet && packet.attribution !== "Not applicable" && (
                          <span className={`attribution attribution-${packet.attribution.split(" ")[0].toLowerCase()}`} title="How clearly the cited work traces to the candidate, not their ability.">Attribution: {packet.attribution}</span>
                        )}
                        <ChevronDown size={12} className="req-chevron" />
                      </div>
                      <p>{packet?.summary ?? requirement.reason}</p>
                      {quote && <small className="req-quote" title={quote}>From the job description: “{quote}”</small>}
                    </button>
                    {skill && <LadderPill level={skill.level} />}
                    <Link className="view-evidence-link" to={`/recruiter/${passport.id}/evidence?skill=${requirement.skill_id}`} aria-label={`View ${requirement.skill_id} evidence`}><FileSearch size={15} /> Evidence</Link>
                  </article>
                  {open && packet && <PacketDetails packet={packet} reason={requirement.reason} />}
                </div>
              );
            })}
          </div>
          <p className="attribution-note">Attribution reflects how clearly the cited work traces to the candidate, not their ability.</p>
        </section>
        <aside className="recruiter-side">
          <section className="panel verified-summary">
            <div className="panel-heading"><div><div className="eyebrow">STRONGEST EVIDENCE</div><h2>Skills with the strongest evidence</h2></div><ShieldCheck size={19} color="#2fb36b" /></div>
            {topSkills.length ? topSkills.map((skill) => {
              const evidence = skill.evidence.find((item) => item.type === "code") ?? skill.evidence[0];
              const where = evidence.start_line > 0
                ? `${evidence.repo}/${evidence.file_path} · L${evidence.start_line}–L${evidence.end_line}`
                : `${evidence.repo}/${evidence.file_path}`;
              const flags = skill.evidence.flatMap((item) => item.integrity ?? []).filter((signal) => signal.status === "warn").length;
              return (
                <Link className="strength-row" to={`/recruiter/${passport.id}/evidence?skill=${skill.skill_id}`} key={skill.skill_id}>
                  <span><strong>{skillLabel(skill)}{flags > 0 && <span className="integrity-flag" title="Authorship signals need a human look"><AlertTriangle size={11} /> {flags} to check</span>}</strong><small>{where}</small></span>
                  <LadderPill level={skill.level} />
                </Link>
              );
            }) : <p className="muted">No evidence was found in the analysed files yet.</p>}
          </section>
          <section className="panel claimed-summary">
            <div className="panel-heading"><div><div className="eyebrow">NOT INCLUDED IN MATCH</div><h2>Claimed, unverified</h2></div><span className="count-tag">{claimedSkills.length}</span></div>
            {claimedSkills.length ? (
              <>
                <div className="claimed-inline">{claimedSkills.slice(0, 5).map((skill) => <span key={skill.skill_id}>{skillLabel(skill)}</span>)}</div>
                <p>These skills may be listed on the resume, certificates or project reports, found only in uncommitted files, or retained from an earlier check. No current evidence was found in the analysed files.</p>
              </>
            ) : <p>No unverified claims in this passport.</p>}
          </section>
          <details className="panel interview-summary">
            <summary><span><div className="eyebrow">SHOWN TO THE STUDENT FOR PRACTICE</div><strong>Student practice questions</strong></span><span className="count-tag">{interviews.length} skills</span></summary>
            <p className="muted">The candidate has seen these questions (without the answer notes). Use the private interview kit below for the interview itself.</p>
            {interviews.length ? interviews.slice(0, 5).map((entry) => (
              <article className="interview-entry" key={entry.skill_id}>
                <strong>{skillName(entry.skill_id)}</strong>
                {entry.questions.map((question, index) => (
                  <div className="question" key={question.q}><p>{index + 1}. {question.q}</p><small>Good answer mentions: {question.good_answer_mentions}</small></div>
                ))}
              </article>
            )) : <p className="muted">{kitUnavailable(passport) ? KIT_UNAVAILABLE : "Interview prompts will appear when verified code evidence is available."}</p>}
          </details>
          <ReportsPanel reports={passport.reports} portfolio={passport.portfolio} audience="recruiter" />
          <SkillDepthPanel results={passport.skill_depth} />
          <SqlDepthPanel result={passport.sql_depth} audience="recruiter" compact />
        </aside>
      </div>
      <InterviewWorkspace passportId={passport.id} />
      <footer className="recruiter-disclaimer"><ShieldCheck size={14} /> Applied and Demonstrated skills link to code the candidate authored. Used reflects dependencies or configuration; unverified claims are never counted. <Link to={`/recruiter/${passport.id}/evidence`}><ExternalLink size={13} /> Full evidence review</Link></footer>
    </div>
  );
}
