import { Download, RefreshCw, ShieldCheck, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { api, isMissingJobError, skillLabel, type Passport, type PassportSkill } from "../api";
import CoverageSummary from "../components/CoverageSummary";
import EvidencePanel from "../components/EvidencePanel";
import GrowthCard from "../components/GrowthCard";
import LadderPill from "../components/LadderPill";
import RejectedClaims, { type Audience } from "../components/RejectedClaims";
import { LEVEL_HELP_STUDENT, LEVEL_NOTE } from "../levels";
import { GROWTH_UNAVAILABLE, growthUnavailable } from "../passportNotes";
import { skillName } from "../skillNames";
import { nextJobPollDelay } from "../jobPolling";

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
  const [changes, setChanges] = useState<NonNullable<Passport["level_changes"]>>([]);

  const load = useCallback(async () => {
    const data = audience === "recruiter" ? await api.reviewPassport(id) : await api.passport(id);
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
    load().catch((reason: Error) => setError(reason.message));
  }, [load]);

  useEffect(() => {
    const pending = api.pendingRecheckId(id);
    if (pending) {
      setRecheckJobId(pending);
      setBusy(true);
    }
  }, [id]);

  useEffect(() => {
    if (!recheckJobId) return;
    let active = true;
    let timer = 0;
    let delay = 1500;
    const poll = async () => {
      try {
        const job = await api.job(recheckJobId);
        if (!active) return;
        if (job.status === "done") {
          await load();
          if (active) {
            setError("");
            setRecheckJobId(null);
            setBusy(false);
          }
          return;
        }
        if (job.status === "failed") {
          setError(job.error || "Re-check failed. Please try again.");
          setRecheckJobId(null);
          setBusy(false);
          return;
        }
        delay = 1500;
      } catch (reason) {
        if (!active) return;
        if (isMissingJobError(reason)) {
          api.clearPendingRecheck(recheckJobId);
          setError("This re-check is no longer available. Please try again.");
          setRecheckJobId(null);
          setBusy(false);
          return;
        }
        setError("Connection interrupted. Retrying re-check status automatically…");
        delay = nextJobPollDelay(delay);
      }
      timer = window.setTimeout(poll, delay);
    };
    timer = window.setTimeout(poll, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [load, recheckJobId]);

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
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Re-check failed.");
      setBusy(false);
    }
  }

  async function downloadBadge() {
    try {
      const credential = await api.badge(id);
      const blob = new Blob([JSON.stringify(credential, null, 2)], { type: "application/ld+json" });
      const link = document.createElement("a");
      const url = URL.createObjectURL(blob);
      link.href = url;
      link.download = `skill-passport-${id}.json`;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not download the credential.");
    }
  }

  if (error && !passport) return <div className="page-message notice-error">{error}</div>;
  if (!passport) return <div className="page-message"><span className="spin"><RefreshCw size={18} /></span> Loading evidence…</div>;

  return (
    <div className="passport-page">
      <header className="passport-heading">
        <div>
          <div className="eyebrow">STUDENT PASSPORT · CREATED {new Date(passport.created_at).toLocaleDateString()}{passport.updated_at && passport.updated_at.slice(0, 10) !== passport.created_at.slice(0, 10) ? ` · RE-CHECKED ${new Date(passport.updated_at).toLocaleDateString()}` : ""}</div>
          <h1>@{passport.github_username}{passport.is_demo && <span className="demo-badge">DEMO DATA</span>}</h1>
          <p>Target role: <strong>{passport.target_role}</strong> <span className="source-summary">GitHub: {passport.sources.repos} repos · Resume PDF · {passport.sources.certificates} certificates</span></p>
          <CoverageSummary coverage={passport.coverage} />
        </div>
        <div className="passport-actions">
          <button className="button button-light" onClick={downloadBadge}><Download size={15} /> Download credential</button>
        </div>
      </header>
      {error && <div className="notice notice-error">{error}</div>}
      {changes.length > 0 && (
        <div className="upgrade-banner"><Sparkles size={18} /><div><strong>Evidence updated after re-check</strong><p>{changes.map((change) => `${skillName(change.skill_id)}: ${change.from} → ${change.to}`).join(" · ")}</p></div></div>
      )}
      <section className="ladder-legend">
        <div className="legend-title"><ShieldCheck size={17} /> EVIDENCE LADDER <small>Levels come from fixed evidence rules, never AI.</small></div>
        <div className="legend-items">{ladder.map(([level, text]) => (
          <div className="legend-item" key={level}><LadderPill level={level} /><span>{text}</span></div>
        ))}</div>
        <p className="legend-note">{LEVEL_NOTE}</p>
      </section>
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
              <div className="panel-heading"><div><div className="eyebrow">SEPARATE · NEVER COUNTED AS MET</div><h2>On your resume, not yet evidenced</h2></div><span className="count-tag">{claimed.length}</span></div>
              <div className="claimed-skills">{claimed.map((skill) => (
                <button key={skill.skill_id} onClick={() => setSelected(skill)}><span>{skillLabel(skill)}</span><LadderPill level="Claimed" /></button>
              ))}</div>
              <p className="muted">Listed on resume or certificates, or found only in files you did not commit to. No qualifying evidence was found.</p>
            </section>
          )}
          <section className="panel growth-panel">
            <div className="panel-heading"><div><div className="eyebrow">GROWTH LOOP</div><h2>Turn gaps into proof</h2></div></div>
            <p className="muted">Complete a task in your repo, then re-ingest GitHub to check for new evidence.</p>
            {passport.growth_tasks.length ? passport.growth_tasks.map((task) => (
              <GrowthCard key={task.skill_id} task={task} onRecheck={recheck} busy={busy} />
            )) : growthUnavailable(passport)
              ? <div className="notice notice-warning">{GROWTH_UNAVAILABLE}</div>
              : <div className="empty-state">No role gaps found. Keep building and use Re-check when your work changes.</div>}
            <button className="button recheck-button" onClick={recheck} disabled={busy}><RefreshCw size={15} className={busy ? "spin" : ""} /> {busy ? "Re-checking GitHub…" : "Re-check GitHub now"}</button>
          </section>
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
