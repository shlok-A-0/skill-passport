import { ArrowRight, Clipboard, ExternalLink, FileUp, Github, KeyRound, LoaderCircle, ShieldCheck } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, skillLabel, type CustomRole, type OwnershipChallenge, type PassportJob, type Role } from "../api";
import { droppedSummary, effectiveTargetRole, extractionNote, JOB_DESCRIPTION_OPTION, jobDescriptionInputError } from "../customRole";
import { sameGitHubUsername } from "../ownership";
import { validatePassportForm } from "../passportForm";

const PROOF_REQUIRED = "Prove this GitHub account is yours first: get a verification code and publish it as a public gist.";

const steps = [
  "Reading GitHub",
  "Reading resume",
  "Reading certificates",
  "Reading reports",
  "Proposing skills",
  "Verifying evidence",
  "Matching role",
  "Building growth plan",
  "Done",
];

export default function CreatePassport({ job, onJobChange, pollingPaused = false, generationError = "", onRetryPolling }: {
  job: PassportJob | null;
  onJobChange: (job: PassportJob) => void;
  pollingPaused?: boolean;
  generationError?: string;
  onRetryPolling: () => void;
}) {
  const [roles, setRoles] = useState<Role[]>([]);
  const [roleError, setRoleError] = useState("");
  const [username, setUsername] = useState("");
  const [role, setRole] = useState("");
  const [resume, setResume] = useState<File | null>(null);
  const [certificates, setCertificates] = useState<File[]>([]);
  const [demo, setDemo] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [challenge, setChallenge] = useState<OwnershipChallenge | null>(null);
  const [gettingCode, setGettingCode] = useState(false);
  const [copied, setCopied] = useState(false);
  const [customRolesEnabled, setCustomRolesEnabled] = useState(false);
  const [reportsEnabled, setReportsEnabled] = useState(false);
  const [reports, setReports] = useState<File[]>([]);
  const [portfolioUrl, setPortfolioUrl] = useState("");
  const [jdTitle, setJdTitle] = useState("");
  const [jdText, setJdText] = useState("");
  const [extracted, setExtracted] = useState<CustomRole | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [extractError, setExtractError] = useState("");
  const usernameRef = useRef(username);
  const challengeRequestId = useRef(0);
  const extractRequestId = useRef(0);
  const usingJobDescription = role === JOB_DESCRIPTION_OPTION;

  function changeJobDescription(title: string, text: string) {
    // Editing the title or text invalidates the extraction, including one still in flight.
    extractRequestId.current += 1;
    setJdTitle(title);
    setJdText(text);
    setExtracted(null);
    setExtracting(false);
    setExtractError("");
  }

  async function extractRequirements() {
    const inputError = jobDescriptionInputError(jdTitle, jdText);
    if (inputError) {
      setExtractError(inputError);
      return;
    }
    const requestId = ++extractRequestId.current;
    setExtracting(true);
    setExtractError("");
    try {
      const result = await api.roleFromDescription(jdTitle.trim(), jdText.trim());
      if (requestId === extractRequestId.current) setExtracted(result);
    } catch (reason) {
      if (requestId === extractRequestId.current) {
        setExtractError(reason instanceof Error ? reason.message : "Could not extract requirements.");
      }
    } finally {
      if (requestId === extractRequestId.current) setExtracting(false);
    }
  }

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
    // Optional features stay hidden if this call fails.
    api.features().then((features) => {
      setCustomRolesEnabled(features.custom_roles);
      setReportsEnabled(features.reports);
    }).catch(() => {
      setCustomRolesEnabled(false);
      setReportsEnabled(false);
    });
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (usingJobDescription && !extracted) {
      setError("Extract the requirements from the job description first.");
      return;
    }
    const targetRole = effectiveTargetRole(role, extracted?.id ?? null);
    const sentReports = reportsEnabled ? reports : [];
    const sentPortfolio = reportsEnabled ? portfolioUrl.trim() : "";
    const validationError = validatePassportForm({
      username, resume, role: targetRole, certificates, reports: sentReports, portfolioUrl: sentPortfolio,
    });
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
    data.append("target_role", targetRole);
    data.append("is_demo", String(demo));
    if (!demo && challenge) data.append("ownership_challenge_id", challenge.challenge_id);
    certificates.forEach((file) => data.append("certificates", file));
    sentReports.forEach((file) => data.append("reports", file));
    if (sentPortfolio) data.append("portfolio_url", sentPortfolio);
    try {
      const created = await api.createPassport(data);
      if (!demo) setChallenge(null); // each code works once
      onJobChange({
        job_id: created.job_id,
        status: "queued",
        step: "Queued",
        completed_steps: [],
        error: null,
      });
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
              {customRolesEnabled && <option value={JOB_DESCRIPTION_OPTION}>Paste a job description</option>}
            </select>
          </label>
          {usingJobDescription && customRolesEnabled && (
            <div className="jd-box">
              <label>Job title
                <span className="input-wrap"><input value={jdTitle} maxLength={120} onChange={(event) => changeJobDescription(event.target.value, jdText)} placeholder="e.g. Backend Engineer Intern" autoComplete="off" /></span>
              </label>
              <label>Job description <span className="muted">50–8,000 characters · {jdText.trim().length.toLocaleString("en-US")} used</span>
                <textarea className="jd-textarea" value={jdText} rows={7} onChange={(event) => changeJobDescription(jdTitle, event.target.value)} placeholder="Paste the full job description" />
              </label>
              <button className="button button-light" type="button" onClick={extractRequirements} disabled={extracting}>
                {extracting ? <LoaderCircle className="spin" size={14} /> : null} {extracting ? "Extracting…" : "Extract requirements"}
              </button>
              <p className="privacy-note">The description is sent to the AI to suggest requirements; it is not stored. Minimum levels are set by fixed rules, and every quote is checked against your text.</p>
              {extractError && <div className="notice notice-error" role="alert">{extractError}</div>}
              {extracted && (
                <div className="jd-requirements" aria-live="polite">
                  <strong>{extracted.requirements.length} requirements for {extracted.title}</strong>
                  <ul>
                    {extracted.requirements.map((item) => (
                      <li key={item.skill_id}>
                        <span><b>{skillLabel(item)}</b> <span className={`importance importance-${item.importance}`}>{item.importance}</span> <span className="muted">minimum {item.min_level}</span></span>
                        <small className="muted">“{item.quote}”</small>
                      </li>
                    ))}
                  </ul>
                  {droppedSummary(extracted.dropped.length) && (
                    <details className="muted">
                      <summary>{droppedSummary(extracted.dropped.length)}</summary>
                      <ul>{extracted.dropped.map((item, index) => <li key={index}>{item.quote ? `“${item.quote}”` : item.skill_id ?? "Unnamed"}: {item.reason}</li>)}</ul>
                    </details>
                  )}
                  {extractionNote(extracted) && <p className="muted">{extractionNote(extracted)}</p>}
                </div>
              )}
            </div>
          )}
          <label className="upload-box">
            <span className="upload-icon"><FileUp size={20} /></span>
            <strong>{resume ? resume.name : "Upload your resume"}</strong>
            <small>PDF · Max 10 MB</small>
            <input type="file" accept="application/pdf,.pdf" onChange={(event) => { setResume(event.target.files?.[0] ?? null); setError(""); }} />
          </label>
          <label className="cert-upload">Certificates <span className="muted">optional · up to 5 PDFs or images · select multiple</span>
            <input type="file" accept="application/pdf,.pdf,image/png,.png,image/jpeg,.jpg,.jpeg" multiple disabled={certificates.length >= 5} onChange={(event) => {
              const selected = Array.from(event.currentTarget.files ?? []);
              event.currentTarget.value = "";
              const remaining = 5 - certificates.length;
              setCertificates([...certificates, ...selected.slice(0, remaining)]);
              setError(selected.length > remaining
                ? "Upload no more than 5 certificates. Extra selected files were not added."
                : "");
            }} />
          </label>
          {reportsEnabled && (
            <>
              <label className="cert-upload">Project reports <span className="muted">optional · up to 3 PDFs or images · checked against your code, never raise a level</span>
                <input type="file" accept="application/pdf,.pdf,image/png,.png,image/jpeg,.jpg,.jpeg" multiple disabled={reports.length >= 3} onChange={(event) => {
                  const selected = Array.from(event.currentTarget.files ?? []);
                  event.currentTarget.value = "";
                  const remaining = 3 - reports.length;
                  setReports([...reports, ...selected.slice(0, remaining)]);
                  setError(selected.length > remaining
                    ? "Upload no more than 3 project reports. Extra selected files were not added."
                    : "");
                }} />
              </label>
              {reports.length > 0 && <ul className="certificate-list" aria-label="Selected project reports">
                {reports.map((report, index) => <li key={`${report.name}-${report.lastModified}-${index}`}>
                  <span>{report.name}</span>
                  <button type="button" onClick={() => { setReports((current) => current.filter((_, itemIndex) => itemIndex !== index)); setError(""); }}>Remove</button>
                </li>)}
              </ul>}
              <label className="cert-upload">Portfolio URL <span className="muted">optional · https only · never fetched</span>
                <input type="url" value={portfolioUrl} maxLength={300} placeholder="https://your-username.github.io" onChange={(event) => { setPortfolioUrl(event.target.value); setError(""); }} />
              </label>
            </>
          )}
          <p className="privacy-note">Text PDFs are read locally. Scanned PDFs and certificate{reportsEnabled ? " or report" : ""} images are sent to Google Cloud Vision for text extraction.{reportsEnabled ? " Report text is sent to the AI (personal details removed) to suggest skill claims; it is not stored." : ""}</p>
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
          {(error || generationError || (job?.status === "failed" ? job.error : "")) && <div className="notice notice-error" role="alert">{error || generationError || job?.error}</div>}
          <button className="button submit-button" type="submit" disabled={submitting || (usingJobDescription && !extracted) || Boolean(job && (job.status === "queued" || job.status === "running"))}>
            {submitting ? <LoaderCircle className="spin" size={17} /> : null}
            {submitting ? "Starting…" : "Generate Passport"} <ArrowRight size={17} />
          </button>
        </form>
        {job && (
          <section className="progress-box" aria-live="polite">
            {job.status === "done" && job.passport_id ? (
              <div className="progress-title">Your passport was generated while you were away. <Link to={`/passport/${job.passport_id}`}>View passport</Link></div>
            ) : job.status === "failed" ? (
              <div className="progress-title"><span aria-hidden="true">!</span> Passport generation stopped during {job.step}.</div>
            ) : <div className="progress-title"><LoaderCircle className={job.status === "running" && !pollingPaused ? "spin" : ""} size={17} /> {pollingPaused ? "Passport job status unavailable" : job.step === "Queued" ? "Preparing your evidence…" : job.step}</div>}
            {pollingPaused && <button type="button" className="button button-light" onClick={onRetryPolling}>Retry status check</button>}
            {job.status !== "failed" && job.status !== "done" && (
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
