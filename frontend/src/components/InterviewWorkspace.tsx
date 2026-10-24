import { AlertTriangle, ExternalLink, Eye, EyeOff, Lock, MessagesSquare, RefreshCw, Wrench } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, type InterviewOutcome, type InterviewQuestion, type InterviewSession } from "../api";
import { anchorLines, defaultSessionId, INTERVIEW_DISCLAIMER, newIdempotencyKey, OUTCOME_ORDER, sessionLabel } from "../interview";

const OUTCOME_LABELS: Record<InterviewOutcome, string> = {
  explained_well: "Explained well",
  partly_explained: "Partly explained",
  could_not_explain: "Couldn't explain",
};

function QuestionCard({ question, index, session, onSaved }: {
  question: InterviewQuestion;
  index: number;
  session: InterviewSession;
  onSaved: (question: InterviewQuestion) => void;
}) {
  const [showGuidance, setShowGuidance] = useState(false);
  const [note, setNote] = useState(question.assessment?.note ?? "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const anchor = question.anchor;

  async function save(outcome: InterviewOutcome) {
    setSaving(true);
    setError("");
    try {
      const { assessment } = await api.assessInterviewQuestion(session.id, question.id, outcome, note);
      onSaved({ ...question, assessment });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save the outcome.");
    } finally {
      setSaving(false);
    }
  }

  const noteChanged = question.assessment ? note.trim() !== question.assessment.note : false;

  return (
    <article className={`iq-card ${question.stale ? "iq-stale" : ""}`}>
      <div className="iq-code">
        {anchor ? (
          <>
            <div className="iq-anchor">
              <span title={`Commit ${anchor.commit_sha ?? "unknown"}`}>{anchor.repo}/{anchor.file_path} · L{anchor.focus_start_line}–L{anchor.focus_end_line}</span>
              {anchor.focus_url && <a href={anchor.focus_url} target="_blank" rel="noreferrer">Open on GitHub <ExternalLink size={10} /></a>}
            </div>
            <pre className="code-snippet">{anchorLines(anchor).map((line) => (
              <span className={`code-line ${line.focus ? "code-line-matched" : ""}`} key={line.number}><i>{line.number}</i>{line.text || " "}</span>
            ))}</pre>
            {question.attribution_warnings.length > 0 && (
              <p className="iq-warning"><AlertTriangle size={11} /> Attribution signals on this citation: {question.attribution_warnings.join(" · ")}</p>
            )}
          </>
        ) : (
          <div className="empty-state">
            No code citation for this requirement in the analysed files. Use this as a recruiter-led discussion; missing evidence is not evidence of a missing skill.
            {question.related_url && <> <a href={question.related_url} target="_blank" rel="noreferrer">Related file <ExternalLink size={10} /></a></>}
          </div>
        )}
      </div>
      <div className="iq-body">
        <div className="iq-meta">
          <span className="iq-number">Q{index + 1}</span>
          <strong>{question.skill_name}</strong>
          {question.requirement && <span className={`importance importance-${question.requirement.importance}`}>{question.requirement.importance}</span>}
          <span className="iq-type">{question.type_label}</span>
        </div>
        <ul className="iq-reasons">{question.selection_reasons.map((reason) => <li key={reason}>{reason}</li>)}</ul>
        {question.requirement?.quote && <small className="req-quote" title={question.requirement.quote}>From the job description: “{question.requirement.quote}”</small>}
        {question.stale && <div className="notice notice-warning">{question.stale_reason}</div>}
        <p className="iq-question">{question.question}</p>
        {question.follow_up && <p className="iq-follow-up"><span>Follow-up</span> {question.follow_up}</p>}
        {question.task && (
          <details className="iq-task">
            <summary><Wrench size={12} /> Five-minute code-change task</summary>
            <p>{question.task.statement}</p>
            <p><strong>Expected behaviour:</strong> {question.task.expected_behavior}</p>
            <strong>Acceptance criteria</strong>
            <ul>{question.task.acceptance_criteria.map((item) => <li key={item}>{item}</li>)}</ul>
            <strong>Suggested tests</strong>
            <ul>{question.task.suggested_tests.map((item) => <li key={item}>{item}</li>)}</ul>
            {showGuidance && <p className="iq-guidance-text"><Lock size={10} /> {question.task.evaluation_guidance}</p>}
            <small className="muted">The candidate makes the change in their own editor; nothing is run on this server.</small>
          </details>
        )}
        <button type="button" className="link-button iq-reveal" onClick={() => setShowGuidance(!showGuidance)} aria-expanded={showGuidance}>
          {showGuidance ? <EyeOff size={12} /> : <Eye size={12} />} {showGuidance ? "Hide answer guidance" : "Show answer guidance (recruiter only)"}
        </button>
        {showGuidance && (
          <div className="iq-guidance">
            <span>A good answer covers (guidance, not an answer key):</span>
            <ul>{question.answer_points.map((point) => <li key={point}>{point}</li>)}</ul>
          </div>
        )}
        <div className="iq-assess" role="group" aria-label={`Outcome for question ${index + 1}`}>
          {OUTCOME_ORDER.map((outcome) => (
            <button type="button" key={outcome} disabled={saving}
              className={`iq-outcome iq-${outcome} ${question.assessment?.outcome === outcome ? "iq-selected" : ""}`}
              aria-pressed={question.assessment?.outcome === outcome}
              onClick={() => save(outcome)}>{OUTCOME_LABELS[outcome]}</button>
          ))}
        </div>
        <div className="iq-note">
          <input value={note} maxLength={500} placeholder="Optional note (what they said, what was unclear)" onChange={(event) => setNote(event.target.value)} aria-label={`Note for question ${index + 1}`} />
          {noteChanged && question.assessment && <button type="button" className="button button-light" disabled={saving} onClick={() => save(question.assessment!.outcome)}>Save note</button>}
        </div>
        {question.assessment
          ? <small className="muted">Saved {new Date(question.assessment.assessed_at).toLocaleString()} by {question.assessment.assessor_id}</small>
          : <small className="muted">Not yet assessed.</small>}
        {error && <div className="notice notice-error">{error}</div>}
      </div>
    </article>
  );
}

/** Private recruiter interview sets: generated on request, saved, and assessed question by question. */
export default function InterviewWorkspace({ passportId }: { passportId: string }) {
  const [sessions, setSessions] = useState<InterviewSession[] | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{ message: string; retry?: () => void } | null>(null);
  // Kept until the server answers, so retrying after a dropped connection can't create a second set.
  const pendingKey = useRef<string | null>(null);

  const loadSessions = useCallback(() => {
    let active = true;
    setLoading(true);
    setError(null);
    api.interviewSessions(passportId)
      .then(({ items }) => {
        if (active) {
          setSessions(items);
          setActiveId((current) => current ?? defaultSessionId(items));
        }
      })
      .catch((reason: Error) => {
        if (active) {
          setSessions((current) => current ?? []);
          setError({
            message: reason.message || "Could not load interview sets.",
            retry: loadSessions,
          });
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [passportId]);

  useEffect(() => {
    setSessions(null);
    return loadSessions();
  }, [loadSessions]);

  // A set still generating (e.g. the page was reloaded mid-generation) appears once it's saved.
  // Polling stops by itself: the server reports a set stuck for 5 minutes as failed.
  const anyGenerating = Boolean(sessions?.some((item) => item.status === "generating"));
  useEffect(() => {
    if (!anyGenerating) return;
    let active = true;
    const timer = window.setInterval(() => {
      api.interviewSessions(passportId)
        .then(({ items }) => { if (active) setSessions(items); })
        .catch(() => undefined);
    }, 3000);
    return () => { active = false; window.clearInterval(timer); };
  }, [anyGenerating, passportId]);

  async function generate() {
    if (generating) return;
    setGenerating(true);
    setError(null);
    pendingKey.current ??= newIdempotencyKey();
    try {
      const session = await api.createInterviewSession(passportId, pendingKey.current);
      pendingKey.current = null;
      setSessions((current) => [session, ...(current ?? []).filter((item) => item.id !== session.id)]);
      setActiveId(session.id);
    } catch (reason) {
      // The server answered (failed or busy): the next click is a new attempt with a new key.
      if (reason instanceof ApiError && reason.status !== 0) pendingKey.current = null;
      setError({
        message: reason instanceof Error ? reason.message : "Could not generate an interview set.",
        retry: generate,
      });
      api.interviewSessions(passportId).then(({ items }) => setSessions(items)).catch(() => undefined);
    } finally {
      setGenerating(false);
    }
  }

  function updateQuestion(question: InterviewQuestion) {
    // Reload so the summary always comes from the saved outcomes.
    setSessions((current) => (current ?? []).map((item) => item.id !== activeId ? item : {
      ...item,
      questions: item.questions.map((existing) => existing.id === question.id ? question : existing),
    }));
    if (activeId) {
      api.interviewSession(activeId)
        .then((fresh) => setSessions((current) => (current ?? []).map((item) => item.id === fresh.id ? fresh : item)))
        .catch((reason: Error) => setError({ message: reason.message }));
    }
  }

  const session = sessions?.find((item) => item.id === activeId) ?? null;
  const readySets = sessions?.filter((item) => item.status === "ready").length ?? 0;

  return (
    <section className="panel interview-workspace" id="interview" aria-labelledby="interview-heading">
      <div className="panel-heading">
        <div>
          <div className="eyebrow"><Lock size={10} /> PRIVATE · RECRUITER ONLY · DEFEND-YOUR-WORK INTERVIEW</div>
          <h2 id="interview-heading">Interview kit</h2>
        </div>
        <div className="interview-actions">
          {session?.status === "ready" && <span className="interview-summary-pill">{session.summary.text}</span>}
          <button type="button" className="button" onClick={generate} disabled={generating || loading || sessions === null}>
            {generating ? <><RefreshCw size={14} className="spin" /> Generating…</> : <><MessagesSquare size={14} /> {readySets ? "Generate another set" : "Generate interview set"}</>}
          </button>
        </div>
      </div>
      <details className="interview-how">
        <summary>How questions are picked</summary>
        <p className="muted">Questions are picked from the job requirements and the candidate's cited code: job-critical requirements whose attribution needs a look come first, strong evidence last. Each question is checked against the cited file and lines before it's saved.</p>
      </details>
      <p className="muted interview-note">{INTERVIEW_DISCLAIMER}</p>
      {error && <div className="notice notice-error">{error.message} {!generating && !loading && error.retry && <button type="button" className="link-button" onClick={error.retry}>Try again</button>}</div>}
      {sessions && sessions.length > 1 && (
        <label className="interview-picker">Interview set
          <select value={activeId ?? ""} onChange={(event) => setActiveId(event.target.value)}>
            {sessions.map((item, index) => <option key={item.id} value={item.id}>{sessionLabel(item, index, sessions.length)}</option>)}
          </select>
        </label>
      )}
      {sessions === null && <p className="muted">Loading interview sets…</p>}
      {sessions && !sessions.length && !generating && !loading && !error && (
        <div className="empty-state">No interview set yet. Generate one to get questions grounded in this candidate's cited code. Refreshing the page never generates new questions.</div>
      )}
      {session?.status === "failed" && <div className="notice notice-error">{session.error ?? "Generation failed."} Generate a new set to try again.</div>}
      {session?.status === "generating" && <p className="muted">This set is still being generated…</p>}
      {session?.status === "ready" && (
        <>
          {session.passport_changed && session.questions.some((item) => item.stale) && (
            <div className="notice notice-warning">The passport was re-checked after this set was generated. Questions marked as outdated refer to citations that are no longer current; generate a new set to ask about the current code.</div>
          )}
          <div className="iq-list">
            {session.questions.map((question, index) => (
              <QuestionCard key={question.id} question={question} index={index} session={session} onSaved={updateQuestion} />
            ))}
          </div>
          {session.dropped.length > 0 && (
            <details className="iq-dropped">
              <summary>{session.dropped.length} generated question{session.dropped.length === 1 ? " was" : "s were"} rejected by validation</summary>
              <ul>{session.dropped.map((item, index) => <li key={index}>{item.skill_id ? `${item.skill_id}: ` : ""}{item.reason}</li>)}</ul>
            </details>
          )}
        </>
      )}
    </section>
  );
}
