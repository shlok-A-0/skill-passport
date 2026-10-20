import { ArrowRight, FileUp, Github, LoaderCircle, ShieldCheck } from "lucide-react";
import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { api, isMissingJobError, type PassportJob, type Role } from "../api";
import { nextJobPollDelay } from "../jobPolling";

const steps = [
  "Reading GitHub",
  "Reading resume",
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
    if (!job || job.status === "done" || job.status === "failed") return;
    let active = true;
    let timer = 0;
    let delay = 1500;
    const poll = async () => {
      try {
        const next = await api.job(job.job_id);
        if (!active) return;
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
  }, [job?.job_id, job?.status, navigate]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (!username.trim() || !resume || !role) {
      setError("Add a GitHub username, a resume PDF, and a target role.");
      return;
    }
    setSubmitting(true);
    const data = new FormData();
    data.append("github_username", username.trim());
    data.append("resume", resume);
    data.append("target_role", role);
    data.append("is_demo", String(demo));
    certificates.forEach((file) => data.append("certificates", file));
    try {
      const created = await api.createPassport(data);
      setJob({
        job_id: created.job_id,
        status: "queued",
        step: "Queued",
        completed_steps: [],
        error: null,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start passport generation.");
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
        <form onSubmit={submit} className="create-form">
          <label>GitHub username
            <span className="input-wrap"><Github size={17} /><input value={username} onChange={(event) => setUsername(event.target.value)} placeholder="e.g. octocat" autoComplete="off" /></span>
          </label>
          <label>Target role
            <select value={role} onChange={(event) => setRole(event.target.value)} disabled={!roles.length}>
              {roles.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </label>
          <label className="upload-box">
            <span className="upload-icon"><FileUp size={20} /></span>
            <strong>{resume ? resume.name : "Upload your resume"}</strong>
            <small>PDF · Max 10 MB</small>
            <input type="file" accept="application/pdf,.pdf" onChange={(event) => setResume(event.target.files?.[0] ?? null)} />
          </label>
          <label className="cert-upload">Certificates <span className="muted">optional · PDF</span>
            <input type="file" accept="application/pdf,.pdf" multiple onChange={(event) => setCertificates(Array.from(event.target.files ?? []))} />
          </label>
          <label className="demo-toggle"><input type="checkbox" checked={demo} onChange={(event) => setDemo(event.target.checked)} /> Mark this passport as <strong>DEMO DATA</strong></label>
          {error && <div className="notice notice-error">{error}</div>}
          <button className="button submit-button" type="submit" disabled={submitting || Boolean(job && job.status !== "failed")}>
            {submitting ? <LoaderCircle className="spin" size={17} /> : null}
            {submitting ? "Starting…" : "Generate Passport"} <ArrowRight size={17} />
          </button>
        </form>
        {job && (
          <section className="progress-box" aria-live="polite">
            <div className="progress-title"><LoaderCircle className={job.status === "running" ? "spin" : ""} size={17} /> {job.step === "Queued" ? "Preparing your evidence…" : job.step}</div>
            <ol className="step-tracker">
              {steps.map((step, index) => (
                <li className={index <= activeIndex ? "step-done" : ""} key={step}>
                  <span>{index < activeIndex ? "✓" : index + 1}</span>{step}
                </li>
              ))}
            </ol>
          </section>
        )}
        <p className="privacy-note">No numeric skill scores. Unverified resume claims stay separate and never count as a match.</p>
      </section>
    </div>
  );
}
