import { ArrowRight, Clipboard, ExternalLink, FileUp, Github, KeyRound, LoaderCircle, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, isMissingJobError, type OwnershipChallenge, type PassportJob, type Role } from "../api";
import { nextJobPollDelay } from "../jobPolling";
import { sameGitHubUsername } from "../ownership";
import { validatePassportForm } from "../passportForm";

const PROOF_REQUIRED = "Prove this GitHub account is yours first: get a verification code and publish it as a public gist.";

const steps = [
  "Reading GitHub",
  "Reading resume",
  "Reading certificates",
  "Proposing skills",
  "Verifying evidence",
  "Matching role",
  "Building growth plan",
  "Done",
];

export default function CreatePassport() {
  const navigate = useNavigate();
  const [roles, setRoles] = useState<Role[]>([]);
  const [roleError, setRoleError] = useState("");
  const [username, setUsername] = useState("");
  const [role, setRole] = useState("");
  const [resume, setResume] = useState<File | null>(null);
  const [certificates, setCertificates] = useState<File[]>([]);
  const [demo, setDemo] = useState(false);
  const [job, setJob] = useState<PassportJob | null>(null);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [challenge, setChallenge] = useState<OwnershipChallenge | null>(null);
  const [gettingCode, setGettingCode] = useState(false);
  const [pollingPaused, setPollingPaused] = useState(false);
  const [copied, setCopied] = useState(false);
  const usernameRef = useRef(username);
  const challengeRequestId = useRef(0);

  function changeUsername(value: string) {
    const changed = !sameGitHubUsername(usernameRef.current, value);
    usernameRef.current = value;
    setUsername(value);
    setError("");
    if (changed) {
      // A code only proves the account it was issued for; invalidate in-flight responses too.
      challengeRequestId.current += 1;
      setChallenge(null);
      setGettingCode(false);
    }
  }

  async function getCode() {
    const requestedUsername = username.trim();
    const requestId = ++challengeRequestId.current;
    setError("");
    setGettingCode(true);
    try {
      const nextChallenge = await api.ownershipChallenge(requestedUsername);
      if (requestId !== challengeRequestId.current || !sameGitHubUsername(requestedUsername, usernameRef.current)) return;
      setChallenge(nextChallenge);
      setCopied(false);
    } catch (reason) {
      if (requestId !== challengeRequestId.current || !sameGitHubUsername(requestedUsername, usernameRef.current)) return;
      setError(reason instanceof Error ? reason.message : "Could not get a verification code.");
    } finally {
      if (requestId === challengeRequestId.current) setGettingCode(false);
    }
  }

  async function copyCode() {
    if (!challenge) return;
    try {
      await navigator.clipboard.writeText(challenge.code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setError("Could not copy the code. Select it and copy it manually.");
    }
  }

  useEffect(() => {
    api.roles().then((items) => {
      setRoles(items);
      if (items.length) setRole(items[0].id);
    }).catch((reason: Error) => setRoleError(reason.message));
  }, []);

  useEffect(() => {
    const pendingJobId = api.pendingJobId();
    if (pendingJobId) {
      setJob({
        job_id: pendingJobId,
        status: "queued",
        step: "Queued",
        completed_steps: [],
        error: null,
      });
    }
  }, []);

  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed" || pollingPaused) return;
    let active = true;
    let timer = 0;
    let delay = 1500;
    let consecutiveFailures = 0;
    const poll = async () => {
      try {
        const next = await api.job(job.job_id);
        if (!active) return;
        consecutiveFailures = 0;
        setError("");
        setJob(next);
        if (next.status === "done" && next.passport_id) {
          navigate(`/passport/${next.passport_id}`);
          return;
        }
        if (next.status === "failed") {
          setError(next.error || "Passport generation failed.");
          return;
        }
        delay = 1500;
      } catch (reason) {
        if (!active) return;
        if (isMissingJobError(reason)) {
          api.clearPendingJob(job.job_id);
          setJob((current) => current?.job_id === job.job_id ? { ...current, status: "failed" } : current);
          setError("This job is no longer available. Please generate the passport again.");
          return;
        }
        consecutiveFailures += 1;
        if (consecutiveFailures >= 8) {
          setPollingPaused(true);
          setError("Could not reach the server to check this job. Your job may still be running; retry the status check when your connection is back.");
          return;
        }
        setError("Connection interrupted. Retrying passport status automatically…");
        delay = nextJobPollDelay(delay);
      }
      timer = window.setTimeout(poll, delay);
    };
    timer = window.setTimeout(poll, 0);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [job?.job_id, job?.status, navigate, pollingPaused]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const validationError = validatePassportForm({ username, resume, role, certificates });
    if (validationError) {
      setError(validationError);
      return;
    }
    if (!demo && (!challenge || !sameGitHubUsername(challenge.github_username, username))) {
      if (challenge) setChallenge(null);
      setError(PROOF_REQUIRED);
      return;
    }
    if (!resume) {
      setError("Upload your resume PDF before generating a passport.");
      return;
    }
    setSubmitting(true);
    const data = new FormData();
    data.append("github_username", username.trim());
    data.append("resume", resume);
    data.append("target_role", role);
    data.append("is_demo", String(demo));
    if (!demo && challenge) data.append("ownership_challenge_id", challenge.challenge_id);
    certificates.forEach((file) => data.append("certificates", file));
    try {
      const created = await api.createPassport(data);
      if (!demo) setChallenge(null); // each code works once
      setJob({
        job_id: created.job_id,
        status: "queued",
        step: "Queued",
        completed_steps: [],
        error: null,
      });
      setPollingPaused(false);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "Could not start passport generation.";
      // An expired, used or unknown code can't be retried; show the "Get verification code" button again.
      if (message.includes("Get a new code")) setChallenge(null);
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }

  const activeIndex = job ? steps.indexOf(job.step) : -1;
  return (
    <div className="create-layout">
      <section className="create-intro">
        <div className="eyebrow">SKILL PASSPORT · PROOF, NOT CLAIMS</div>
        <h1>Your work speaks.<br /><em>Now show the proof.</em></h1>
        <p>Turn your real code into a clear, evidence-backed skill profile. Every verified citation links to the exact lines a recruiter can inspect.</p>
        <div className="promise-list">
          <span><ShieldCheck size={17} /> Human-checkable evidence</span>
          <span><Github size={17} /> Authorship is checked per file</span>
          <span><ArrowRight size={17} /> Every gap becomes a growth task</span>
        </div>
      </section>
      <section className="panel create-card">
        <div className="panel-heading">
          <div><div className="eyebrow">START YOUR PASSPORT</div><h2>Bring your proof</h2></div>
        </div>
        {roleError && <div className="notice notice-error">{roleError}</div>}
        <form onSubmit={submit} className="create-form" noValidate>
          <label>GitHub username
            <span className="input-wrap"><Github size={17} /><input value={username} onChange={(event) => changeUsername(event.target.value)} placeholder="e.g. octocat" autoComplete="off" /></span>
          </label>
          <label>Target role
            <select value={role} onChange={(event) => { setRole(event.target.value); setError(""); }} disabled={!roles.length}>
              {roles.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label className="upload-box">
            <span className="upload-icon"><FileUp size={20} /></span>
            <strong>{resume ? resume.name : "Upload your resume"}</strong>
            <small>PDF · Max 10 MB</small>
            <input type="file" accept="application/pdf,.pdf" onChange={(event) => { setResume(event.target.files?.[0] ?? null); setError(""); }} />
          </label>
          <label className="cert-upload">Certificates <span className="muted">optional · up to 5 PDFs · select multiple</span>
            <input type="file" accept="application/pdf,.pdf" multiple disabled={certificates.length >= 5} onChange={(event) => {
              const selected = Array.from(event.currentTarget.files ?? []);
              event.currentTarget.value = "";
              const remaining = 5 - certificates.length;
              setCertificates([...certificates, ...selected.slice(0, remaining)]);
              setError(selected.length > remaining
                ? "Upload no more than 5 certificates. Extra selected files were not added."
                : "");
            }} />
          </label>
          {certificates.length > 0 && <ul className="certificate-list" aria-label="Selected certificates">
            {certificates.map((certificate, index) => <li key={`${certificate.name}-${certificate.lastModified}-${index}`}>
              <span>{certificate.name}</span>
              <button type="button" onClick={() => { setCertificates((current) => current.filter((_, itemIndex) => itemIndex !== index)); setError(""); }}>Remove</button>
            </li>)}
          </ul>}
          <label className="demo-toggle"><input type="checkbox" checked={demo} onChange={(event) => { setDemo(event.target.checked); setError(""); }} /> Mark this passport as <strong>DEMO DATA</strong></label>
          {!demo && (
            <div className="notice notice-warning">
              <strong><KeyRound size={13} /> Prove this account is yours</strong>
              {challenge ? (
                <>
                  <p>Signed in to GitHub as <strong>{challenge.github_username}</strong>, create a <strong>public</strong> gist with this description (any file name and content). The code works once and expires in 1 hour; you can delete the gist afterwards.</p>
                  <p><code>{challenge.code}</code></p>
                  <button className="button button-light" type="button" onClick={copyCode}><Clipboard size={14} /> {copied ? "Copied" : "Copy"}</button>{" "}
                  <a className="button button-light" href="https://gist.github.com" target="_blank" rel="noreferrer">Open gist.github.com <ExternalLink size={14} /></a>
                </>
              ) : (
                <>
                  <p>Anyone can type a username, so we check that you control this GitHub account with a one-time code you post as a public gist.</p>
                  <button className="button button-light" type="button" onClick={getCode} disabled={!username.trim() || gettingCode}>
                    {gettingCode ? <LoaderCircle className="spin" size={14} /> : <KeyRound size={14} />} Get verification code
                  </button>
                </>
              )}
            </div>
          )}
          {error &&<div className="notice notice-error">{error}</div>}
          <button className="button submit-button" type="submit" disabled={submitting || Boolean(job && job.status !== "failed")}>
            {submitting ? <LoaderCircle className="spin" size={17} /> : null}
            {submitting ? "Starting…" : "Generate Passport"} <ArrowRight size={17} />
          </button>
        </form>
        {job && (
          <section className="progress-box" aria-live="polite">
            {job.status === "failed" ? (
              <div className="progress-title"><span aria-hidden="true">!</span> Passport generation stopped during {job.step}.</div>
            ) : <div className="progress-title"><LoaderCircle className={job.status === "running" && !pollingPaused ? "spin" : ""} size={17} /> {pollingPaused ? "Passport job status unavailable" : job.step === "Queued" ? "Preparing your evidence…" : job.step}</div>}
            {pollingPaused && <button type="button" className="button button-light" onClick={() => { setError(""); setPollingPaused(false); }}>Retry status check</button>}
            {job.status !== "failed" && (
            <ol className="step-tracker">
              {steps.map((step, index) => (
                <li className={index <= activeIndex ? "step-done" : ""} key={step}>
                  <span>{index < activeIndex ? "✓" : index + 1}</span>{step}
                </li>
              ))}
            </ol>
            )}
          </section>
        )}
        <p className="privacy-note">No numeric skill scores. Unverified resume claims stay separate and never count as a match.</p>
      </section>
    </div>
  );
}
