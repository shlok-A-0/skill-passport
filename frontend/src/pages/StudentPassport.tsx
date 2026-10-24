import { FileText, RefreshCw, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api, isMissingJobError, skillLabel, type Passport, type PassportSkill } from "../api";
import CoverageSummary from "../components/CoverageSummary";
import EvidencePanel from "../components/EvidencePanel";
import GrowthCard from "../components/GrowthCard";
import LadderPill from "../components/LadderPill";
import RejectedClaims, { type Audience } from "../components/RejectedClaims";
import ReportsPanel from "../components/ReportsPanel";
import SqlDepthPanel from "../components/SqlDepthPanel";
import SkillDepthPanel from "../components/SkillDepthPanel";
import { LEVEL_HELP_STUDENT, LEVEL_NOTE } from "../levels";
import { GROWTH_UNAVAILABLE, growthUnavailable } from "../passportNotes";
import { skillName } from "../skillNames";
import { MAX_CONSECUTIVE_POLL_FAILURES, nextJobPollDelay, pollJobAttempt } from "../jobPolling";

const ladder = (["Claimed", "Used", "Applied", "Demonstrated"] as const).map(
  (level) => [level, LEVEL_HELP_STUDENT[level]] as const,
);

const levelOrder: Record<PassportSkill["level"], number> = {
  Claimed: 0,
  Used: 1,
  Applied: 2,
  Demonstrated: 3,
};

export default function StudentPassport({ audience = "student" }: { audience?: Audience }) {
  const { id = "" } = useParams();
  const [searchParams] = useSearchParams();
  const [passport, setPassport] = useState<Passport | null>(null);
  const [selected, setSelected] = useState<PassportSkill | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [recheckJobId, setRecheckJobId] = useState<string | null>(null);
  const [pollingPaused, setPollingPaused] = useState(false);
  const [changes, setChanges] = useState<NonNullable<Passport["level_changes"]>>([]);
  const loadVersion = useRef(0);

  const load = useCallback(async () => {
    const version = ++loadVersion.current;
    let data: Passport;
    try {
      data = audience === "recruiter" ? await api.reviewPassport(id) : await api.passport(id);
    } catch (reason) {
      if (version !== loadVersion.current) return;
      throw reason;
    }
    if (version !== loadVersion.current) return;
    setPassport(data);
    const requestedSkill = searchParams.get("skill");
    const strongestSkill = [...data.skills]
      .filter((skill) => skill.level !== "Claimed" && skill.evidence.length > 0)
      .sort((left, right) => levelOrder[right.level] - levelOrder[left.level])[0];
    setSelected(
      data.skills.find((skill) => skill.skill_id === requestedSkill)
        ?? strongestSkill
        ?? data.skills[0]
        ?? null,
    );
    setChanges(data.level_changes ?? []);
  }, [audience, id, searchParams]);

  useEffect(() => {
    setPassport(null);
    setSelected(null);
    setError("");
    load().catch((reason: Error) => setError(reason.message));
    return () => { loadVersion.current += 1; };
  }, [load]);

  useEffect(() => {
    const pending = api.pendingRecheckId(id);
    setRecheckJobId(pending ?? null);
    setBusy(Boolean(pending));
    setPollingPaused(false);
  }, [id]);

  useEffect(() => {
    if (!recheckJobId || pollingPaused) return;
    let active = true;
    let timer = 0;
    let delay = 1500;
    let consecutiveFailures = 0;
    const poll = async () => {
      const attempt = await pollJobAttempt(
        consecutiveFailures,
        () => api.job(recheckJobId),
        async () => { if (active) await load(); },
      );
      if (!active) return;
      consecutiveFailures = attempt.consecutiveFailures;
      if (!attempt.ok) {
        if (isMissingJobError(attempt.error)) {
          api.clearPendingRecheck(recheckJobId);
          setError("This re-check is no longer available. Please try again.");
          setRecheckJobId(null);
          setBusy(false);
          return;
        }
        if (consecutiveFailures >= MAX_CONSECUTIVE_POLL_FAILURES) {
          setPollingPaused(true);
          setError("Could not reach the server to check this re-check. It may still be running; retry the status check when your connection is back.");
          return;
        }
        setError("Connection interrupted. Retrying re-check status automatically…");
        delay = nextJobPollDelay(delay);
      } else if (attempt.job.status === "done") {
        setError("");
        setRecheckJobId(null);
        setBusy(false);
        return;
      } else if (attempt.job.status === "failed") {
        setError(attempt.job.error || "Re-check failed. Please try again.");
        setRecheckJobId(null);
        setBusy(false);
        return;
      } else {
        delay = 1500;
      }
      timer = window.setTimeout(poll, delay);
    };
    timer = window.setTimeout(poll, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [load, recheckJobId, pollingPaused]);

  const verified = useMemo(
    () => passport?.skills.filter((skill) => skill.level !== "Claimed" && skill.evidence.length) ?? [],
    [passport],
  );
  const claimed = useMemo(
    () => passport?.skills.filter((skill) => skill.level === "Claimed" || !skill.evidence.length) ?? [],
    [passport],
  );

  async function recheck() {
    setBusy(true);
    setError("");
    try {
      const job = await api.recheck(id);
      setRecheckJobId(job.job_id);
      setPollingPaused(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Re-check failed.");
      setBusy(false);
    }
  }



  if (error && (!passport || passport.id !== id)) return <div className="page-message notice-error">{error}</div>;
  if (!passport || passport.id !== id) return <div className="page-message"><span className="spin"><RefreshCw size={18} /></span> Loading evidence…</div>;

  return (
    <div className="passport-page">
      <header className="passport-heading">
        <div>
          <div className="eyebrow">CREATED {new Date(passport.created_at).toLocaleDateString()}{passport.updated_at && passport.updated_at.slice(0, 10) !== passport.created_at.slice(0, 10) ? ` · UPDATED ${new Date(passport.updated_at).toLocaleDateString()}` : ""}</div>
          <h1>@{passport.github_username}{passport.is_demo && <span className="demo-badge">DEMO DATA</span>}</h1>
          <p>Target role: <strong>{passport.target_role}</strong> <span className="source-summary">GitHub: {passport.sources.repos} repos · Resume PDF · {passport.sources.certificates} certificates{passport.sources.reports ? ` · ${passport.sources.reports} project report${passport.sources.reports === 1 ? "" : "s"}` : ""}</span></p>
          <CoverageSummary coverage={passport.coverage} />
        </div>
        <div className="passport-actions">
          <Link className="button button-light" to={`/placement?preferredRole=${encodeURIComponent(passport.target_role)}`}>View cohort for this role</Link>
          <Link className="button button-light" to={`/passport/${passport.id}/summary`} target="_blank" rel="noopener noreferrer"><FileText size={15} /> Show summary</Link>
        </div>
      </header>
      {error && <div className="notice notice-error">{error}</div>}
      {changes.length > 0 && (
        <div className="upgrade-banner"><Sparkles size={18} /><div><strong>Evidence updated after re-check</strong><p>{changes.map((change) => `${skillName(change.skill_id)}: ${change.from} → ${change.to}`).join(" · ")}</p></div></div>
      )}
      <details className="ladder-legend">
        <summary>What do the evidence levels mean?</summary>
        <div className="legend-items">{ladder.map(([level, text]) => (
          <div className="legend-item" key={level}><LadderPill level={level} /><span>{text}</span></div>
        ))}</div>
        <p className="legend-note">{LEVEL_NOTE}</p>
      </details>
      <div className="passport-grid">
        <div className="skill-column">
          <section className="panel skill-panel">
            <div className="panel-heading"><div><div className="eyebrow">FROM THE ANALYSED FILES</div><h2>Evidence from your work</h2></div><span className="count-tag">{verified.length} skills</span></div>
            {verified.length ? (
              <div className="skill-grid">{verified.map((skill) => (
                <button className={`skill-tile ${selected?.skill_id === skill.skill_id ? "skill-selected" : ""}`} key={skill.skill_id} onClick={() => setSelected(skill)}>
                  <span className="skill-name">{skillLabel(skill)}</span><LadderPill level={skill.level} />
                  <small>{skill.evidence.length} evidence {skill.certified ? "· Certified" : ""}</small>
                </button>
              ))}</div>
            ) : <div className="empty-state">No evidence was found in the analysed files yet. “Not found” doesn't mean “not skilled”.</div>}
          </section>
          {claimed.length > 0 && (
            <section className="panel claimed-panel">
              <div className="panel-heading"><div><h2>Unverified claims</h2></div><span className="count-tag">{claimed.length}</span></div>
              <div className="claimed-skills">{claimed.map((skill) => (
                <button key={skill.skill_id} onClick={() => setSelected(skill)}><span>{skillLabel(skill)}</span><LadderPill level="Claimed" /></button>
              ))}</div>
              <p className="muted">From documents, uncommitted files or an earlier check. No current evidence in the analysed files. Never counted as met.</p>
            </section>
          )}
          <ReportsPanel reports={passport.reports} portfolio={passport.portfolio} audience={audience} />
          <SkillDepthPanel results={passport.skill_depth} />
          <SqlDepthPanel result={passport.sql_depth} audience={audience} compact />
          <section className="panel growth-panel">
            <div className="panel-heading"><div><h2>Next steps</h2></div></div>
            <p className="muted">Finish a task, then re-check to update your levels.</p>
            {passport.growth_tasks.length ? passport.growth_tasks.map((task) => (
              <GrowthCard key={task.skill_id} task={task} onRecheck={recheck} busy={busy} />
            )) : growthUnavailable(passport)
              ? <div className="notice notice-warning">{GROWTH_UNAVAILABLE}</div>
              : <div className="empty-state">No role gaps found. Keep building and use Re-check when your work changes.</div>}
            {pollingPaused && <button className="button button-light" onClick={() => { setError(""); setPollingPaused(false); }}>Retry status check</button>}
            <button className="button recheck-button" onClick={recheck} disabled={busy}><RefreshCw size={15} className={busy && !pollingPaused ? "spin" : ""} /> {busy ? (pollingPaused ? "Re-check pending" : "Re-checking GitHub…") : "Re-check GitHub"}</button>
          </section>
          {audience === "student" && passport.interview_kit.length > 0 && (
            <section className="panel practice-panel">
              <div className="panel-heading"><div><div className="eyebrow">DEFEND YOUR WORK</div><h2>Practice questions</h2></div><span className="count-tag">{passport.interview_kit.length} skills</span></div>
              <p className="muted">Questions about your own cited code, to help you prepare. A recruiter may ask different questions about any of your work.</p>
              {passport.interview_kit.map((entry) => (
                <article className="interview-entry" key={entry.skill_id}>
                  <strong>{skillName(entry.skill_id)}</strong>
                  {entry.questions.map((question, index) => <div className="question" key={question.q}><p>{index + 1}. {question.q}</p></div>)}
                </article>
              ))}
            </section>
          )}
          <RejectedClaims claims={passport.rejected_claims} audience={audience} passportId={passport.id}
            requests={passport.review_requests} labels={passport.check_labels} onChanged={() => { load().catch((reason: Error) => setError(reason.message)); }} />
        </div>
        <aside className="evidence-column">
          {selected ? <EvidencePanel skill={selected} audience={audience} labels={passport.check_labels} /> :<section className="panel empty-evidence">Select a skill to inspect its evidence trail.</section>}
        </aside>
      </div>
    </div>
  );
}
