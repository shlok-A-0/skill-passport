import { BadgeCheck, BookOpenCheck, BriefcaseBusiness, LayoutDashboard, ShieldCheck } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, isMissingJobError, type PassportJob } from "./api";
import { nextJobPollDelay } from "./jobPolling";
import CreatePassport from "./pages/CreatePassport";
import StudentPassport from "./pages/StudentPassport";
import RecruiterView from "./pages/RecruiterView";
import RecruiterEvidence from "./pages/RecruiterEvidence";

const PlacementCell = lazy(() => import("./pages/PlacementCell"));

function LatestPassportRedirect({ view }: { view: "passport" | "recruiter" }) {
  const [passportId, setPassportId] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (view === "passport") {
      setPassportId(api.latestPassportId() ?? "");
      setLoading(false);
      return;
    }
    api.reviewPassports()
      .then((page) => setPassportId(page.items[0]?.id ?? ""))
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false));
  }, [view]);

  if (loading) return <div className="page-message">Loading latest passport…</div>;
  if (error) return <div className="notice notice-error">{error}</div>;
  if (!passportId) {
    return <div className="page-message">{view === "passport" ? "No passport in this browser session." : "No passports yet."} <Link to="/">Create one first.</Link></div>;
  }
  return <Navigate replace to={`/${view}/${passportId}`} />;
}

function NotFound() {
  return <section className="page-message"><h1>Page not found</h1><p>This link doesn’t point to a Skill Passport page.</p><Link className="button button-light" to="/">Back to Create</Link></section>;
}

function App() {
  const location = useLocation();
  const navigate = useNavigate();
  const [generationJob, setGenerationJob] = useState<PassportJob | null>(() => api.savedJob());
  const [generationError, setGenerationError] = useState("");
  const [pollingPaused, setPollingPaused] = useState(false);
  const [dismissedCompletionId, setDismissedCompletionId] = useState("");

  useEffect(() => {
    const pendingId = api.pendingJobId();
    if (pendingId) {
      const saved = api.savedJob();
      if (saved?.job_id === pendingId) setGenerationJob(saved);
      else setGenerationJob({ job_id: pendingId, status: "queued", step: "Queued", completed_steps: [], error: null });
      setPollingPaused(false);
    }
  }, [location.pathname]);

  useEffect(() => {
    if (!generationJob || generationJob.status === "done" || generationJob.status === "failed" || pollingPaused) return;
    let active = true;
    let timer = 0;
    let delay = 1500;
    let failures = 0;
    const poll = async () => {
      try {
        const next = await api.job(generationJob.job_id);
        if (!active) return;
        failures = 0;
        setGenerationError("");
        setGenerationJob(next);
        if (next.status === "done" && next.passport_id) {
          if (location.pathname === "/") navigate(`/passport/${next.passport_id}`);
          return;
        }
        if (next.status === "failed") {
          setGenerationError(next.error || "Passport generation failed.");
          return;
        }
        delay = 1500;
      } catch (reason) {
        if (!active) return;
        if (isMissingJobError(reason)) {
          api.clearPendingJob(generationJob.job_id);
          const failed = { ...generationJob, status: "failed" as const, error: "This job is no longer available. Please generate the passport again." };
          api.rememberJob(failed);
          setGenerationJob(failed);
          setGenerationError(failed.error ?? "");
          return;
        }
        failures += 1;
        if (failures >= 8) {
          setPollingPaused(true);
          setGenerationError("Could not reach the server to check this job. Your job may still be running; retry the status check when your connection is back.");
          return;
        }
        setGenerationError("Connection interrupted. Retrying passport status automatically…");
        delay = nextJobPollDelay(delay);
      }
      timer = window.setTimeout(poll, delay);
    };
    timer = window.setTimeout(poll, 0);
    return () => { active = false; window.clearTimeout(timer); };
  }, [generationJob?.job_id, generationJob?.status, location.pathname, navigate, pollingPaused]);

  return (
    <div className="app-shell">
      <header className="topbar">
        <Link className="brand" to="/"><span className="brand-mark"><ShieldCheck size={20} /></span><span>Skill Passport<small>PROOF, NOT CLAIMS</small></span></Link>
        <nav aria-label="Primary navigation">
          <NavLink to="/" end>Create</NavLink>
          <NavLink to="/student" className={({ isActive }) => isActive || location.pathname.startsWith("/passport/") ? "active" : undefined}>Student</NavLink>
          <NavLink to="/recruiter">Recruiter</NavLink>
          <NavLink to="/placement">Placement Cell</NavLink>
        </nav>
      </header>
      {generationJob && (generationJob.status === "queued" || generationJob.status === "running") && location.pathname !== "/" && (
        <div className="generation-notice" role="status" aria-live="polite">
          <span>{pollingPaused ? "Passport status check paused" : `Passport generation continues · ${generationJob.step === "Queued" ? "Preparing your evidence…" : generationJob.step}`}</span>
          {pollingPaused && <button type="button" className="link-button" onClick={() => { setGenerationError(""); setPollingPaused(false); }}>Retry status check</button>}
          <Link to="/">View progress</Link>
          {generationError && <small>{generationError}</small>}
        </div>
      )}
      {generationJob?.status === "done" && generationJob.passport_id && location.pathname !== "/" && location.pathname !== `/passport/${generationJob.passport_id}` && dismissedCompletionId !== generationJob.job_id && (
        <div className="generation-notice generation-complete" role="status">
          <span>Your passport is ready.</span><Link to={`/passport/${generationJob.passport_id}`}>View passport</Link>
          <button type="button" className="link-button" onClick={() => setDismissedCompletionId(generationJob.job_id)}>Dismiss</button>
        </div>
      )}
      {generationJob?.status === "failed" && location.pathname !== "/" && (
        <div className="generation-notice generation-failed" role="alert">
          <span>{generationError || generationJob.error || "Passport generation failed."}</span><Link to="/">Try again</Link>
        </div>
      )}
      <main>
        <Routes>
          <Route path="/" element={<CreatePassport job={generationJob} pollingPaused={pollingPaused} generationError={generationError} onRetryPolling={() => { setGenerationError(""); setPollingPaused(false); }} onJobChange={(job) => { api.rememberJob(job); setGenerationJob(job); setGenerationError(""); setPollingPaused(false); setDismissedCompletionId(""); }} />} />
          <Route path="/student" element={<LatestPassportRedirect view="passport" />} />
          <Route path="/recruiter" element={<LatestPassportRedirect view="recruiter" />} />
          <Route path="/passport/:id" element={<StudentPassport />} />
          <Route path="/recruiter/:id" element={<RecruiterView />} />
          <Route path="/recruiter/:id/evidence" element={<RecruiterEvidence />} />
          <Route path="/placement" element={<Suspense fallback={<div className="page-message">Loading placement insights…</div>}><PlacementCell /></Suspense>} />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>
      <footer><BadgeCheck size={16} /> Built on evidence, not self-reported scores <span><BriefcaseBusiness size={15} /> Recruiter-ready</span><span><BookOpenCheck size={15} /> Growth through proof</span><LayoutDashboard size={15} /></footer>
    </div>
  );
}

export default App;
