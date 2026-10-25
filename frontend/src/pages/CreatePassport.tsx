import { ArrowRight, Clipboard, ExternalLink, FileText, FileUp, Github, KeyRound, LoaderCircle, Paperclip } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api, skillLabel, type CustomRole, type OwnershipChallenge, type PassportJob, type Role } from "../api";
import { createScoreNote } from "../sqlDepth";
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

/** Per-letter spans for the home headline entrance; the h1 keeps a plain aria-label for screen readers. */
function headlineLetters(text: string, start: number, extraMs = 0) {
  let seen = 0;
  return [...text].map((char, index) => {
    if (char === " ") return " ";
    const delay = (start + seen++) * 26 + extraMs;
    return <span className="headline-letter" style={{ animationDelay: `${delay}ms` }} key={index}>{char}</span>;
  });
}
const HEADLINE_LINE_1 = "Your work speaks.";
const HEADLINE_LINE_2 = "Now show the proof.";

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
  const [sqlDepthEnabled, setSqlDepthEnabled] = useState(false);
  const [skillDepthEnabled, setSkillDepthEnabled] = useState(false);
  const [reports, setReports] = useState<File[]>([]);
  const [portfolioUrl, setPortfolioUrl] = useState("");
  const [cfHandle, setCfHandle] = useState("");
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
      setSqlDepthEnabled(Boolean(features.sql_depth));
      setSkillDepthEnabled(Boolean(features.skill_depth));
    }).catch(() => {
      setCustomRolesEnabled(false);
      setReportsEnabled(false);
      setSqlDepthEnabled(false);
      setSkillDepthEnabled(false);
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
    const trimmedCfHandle = cfHandle.trim();
    if (trimmedCfHandle && !/^[A-Za-z0-9_.-]{1,24}$/.test(trimmedCfHandle)) {
      setError("Codeforces handles use 1-24 letters, digits, underscore, dot or hyphen.");
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
    if (trimmedCfHandle) data.append("codeforces_handle", trimmedCfHandle);
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
        <h1 aria-label="Your work speaks. Now show the proof.">
          <span aria-hidden="true">
            {headlineLetters(HEADLINE_LINE_1, 0)}
            <br />
            <em>{headlineLetters(HEADLINE_LINE_2, HEADLINE_LINE_1.replace(/ /g, "").length, 420)}</em>
          </span>
        </h1>
        <p>Create a skill profile from your GitHub work and resume, with links to the code behind each verified skill.</p>
      </section>
      <section className="panel create-card">
        <div className="panel-heading">
          <div><h2>Create your passport</h2></div>
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
              <p className="privacy-note">Sent to the AI to suggest requirements; not stored. Levels and quotes are checked by fixed rules.</p>
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
          <details className="optional-uploads"><summary>Optional: Codeforces, certificates, reports and portfolio</summary>
          <label className="cert-upload">Certificates <span className="muted">optional · up to 5 PDFs or images · select multiple</span>
            <span className="file-field">
              <input type="file" className="file-input-hidden" accept="application/pdf,.pdf,image/png,.png,image/jpeg,.jpg,.jpeg" multiple disabled={certificates.length >= 5} onChange={(event) => {
                const selected = Array.from(event.currentTarget.files ?? []);
                event.currentTarget.value = "";
                const remaining = 5 - certificates.length;
                setCertificates([...certificates, ...selected.slice(0, remaining)]);
                setError(selected.length > remaining
                  ? "Upload no more than 5 certificates. Extra selected files were not added."
                  : "");
              }} />
              <span className="file-trigger" aria-disabled={certificates.length >= 5}><Paperclip size={13} /> Choose files</span>
              <span className="file-chosen">{certificates.length ? `${certificates.length} of 5 selected` : "No files selected"}</span>
            </span>
          </label>
          {certificates.length > 0 && <ul className="certificate-list" aria-label="Selected certificates">
            {certificates.map((certificate, index) => <li key={`${certificate.name}-${certificate.lastModified}-${index}`}>
              <span><FileText size={12} /> {certificate.name}</span>
              <button type="button" onClick={() => { setCertificates((current) => current.filter((_, itemIndex) => itemIndex !== index)); setError(""); }}>Remove</button>
            </li>)}
          </ul>}
          <label className="cert-upload">Codeforces handle <span className="muted">optional · public contest data · shown separately, never raises a level · ownership not verified</span>
            <input type="text" value={cfHandle} maxLength={24} placeholder="e.g. tourist" autoComplete="off" onChange={(event) => { setCfHandle(event.target.value); setError(""); }} />
          </label>
          {reportsEnabled && (
            <>
              <label className="cert-upload">Project reports <span className="muted">optional · up to 3 PDFs or images · checked against your code, never raise a level</span>
                <span className="file-field">
                  <input type="file" className="file-input-hidden" accept="application/pdf,.pdf,image/png,.png,image/jpeg,.jpg,.jpeg" multiple disabled={reports.length >= 3} onChange={(event) => {
                    const selected = Array.from(event.currentTarget.files ?? []);
                    event.currentTarget.value = "";
                    const remaining = 3 - reports.length;
                    setReports([...reports, ...selected.slice(0, remaining)]);
                    setError(selected.length > remaining
                      ? "Upload no more than 3 project reports. Extra selected files were not added."
                      : "");
                  }} />
                  <span className="file-trigger" aria-disabled={reports.length >= 3}><Paperclip size={13} /> Choose files</span>
                  <span className="file-chosen">{reports.length ? `${reports.length} of 3 selected` : "No files selected"}</span>
                </span>
              </label>
              {reports.length > 0 && <ul className="certificate-list" aria-label="Selected project reports">
                {reports.map((report, index) => <li key={`${report.name}-${report.lastModified}-${index}`}>
                  <span><FileText size={12} /> {report.name}</span>
                  <button type="button" onClick={() => { setReports((current) => current.filter((_, itemIndex) => itemIndex !== index)); setError(""); }}>Remove</button>
                </li>)}
              </ul>}
              <label className="cert-upload">Portfolio URL <span className="muted">optional · https only · never fetched</span>
                <input type="url" value={portfolioUrl} maxLength={300} placeholder="https://your-username.github.io" onChange={(event) => { setPortfolioUrl(event.target.value); setError(""); }} />
              </label>
            </>
          )}
          </details>
          <label className="demo-toggle"><input type="checkbox" checked={demo} onChange={(event) => { setDemo(event.target.checked); setError(""); }} /> Create as a demo passport (sample data)</label>
          {!demo && (
            <div className="notice notice-warning">
              <strong><KeyRound size={13} /> Prove this account is yours</strong>
              {challenge ? (
                <>
                  <p>While signed in to GitHub as <strong>{challenge.github_username}</strong>, create a <strong>public</strong> gist with this code as the description (any file name and content). It works once and expires in 1 hour; you can delete the gist afterwards.</p>
                  <p><code>{challenge.code}</code></p>
                  <button className="button button-light" type="button" onClick={copyCode}><Clipboard size={14} /> {copied ? "Copied" : "Copy"}</button>{" "}
                  <a className="button button-light" href="https://gist.github.com" target="_blank" rel="noreferrer">Open gist.github.com <ExternalLink size={14} /></a>
                </>
              ) : (
                <>
                  <p>Confirm you control this GitHub account with a one-time code posted as a public gist.</p>
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
        <p className="privacy-note">Unverified resume claims are never counted as a match.</p>
        <details className="privacy-details">
          <summary>How your files and scores are handled</summary>
          <p>Text PDFs are read locally; scanned documents use OCR on the server.{reportsEnabled ? " Report text is sent to the AI (personal details removed) to suggest skill claims; it is not stored." : ""} {createScoreNote(sqlDepthEnabled, skillDepthEnabled)}</p>
        </details>
      </section>
    </div>
  );
  }
