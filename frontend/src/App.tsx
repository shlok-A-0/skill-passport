import {
  BadgeCheck,
  BookOpenCheck,
  BriefcaseBusiness,
  GraduationCap,
  LayoutDashboard,
  LogIn,
  LogOut,
  Repeat,
  ShieldCheck,
} from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, isMissingJobError, type PassportJob } from "./api";
import { AuthProvider, useAuth } from "./auth";
import LoginModal from "./components/LoginModal";
import ProtectedRoute from "./components/ProtectedRoute";
import { nextJobPollDelay } from "./jobPolling";
import CreatePassport from "./pages/CreatePassport";
import StudentPassport from "./pages/StudentPassport";
import StudentSummary from "./pages/StudentSummary";
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
  return (
    <section className="page-message">
      <h1>Page not found</h1>
      <p>This link doesn’t point to a Skill Passport page.</p>
      <Link className="button button-light" to="/">Back to Create</Link>
    </section>
  );
}

function AppContent() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, role, openLoginModal, logout } = useAuth();
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
        <div className="topbar-left">
          <Link className="brand" to="/">
            <span className="brand-mark"><ShieldCheck size={20} /></span>
            <span>Skill Passport<small>PROOF, NOT CLAIMS</small></span>
          </Link>

          <nav aria-label="Primary navigation">
            <NavLink to="/" end>Create</NavLink>
            <NavLink
              to="/student"
              className={({ isActive }) => isActive || location.pathname.startsWith("/passport/") ? "active" : undefined}
            >
              Student
            </NavLink>
            {/* Recruiter and Placement Cell visible only to Recruiter role */}
            {role === "recruiter" && (
              <>
                <NavLink to="/recruiter">Recruiter</NavLink>
                <NavLink to="/placement">Placement Cell</NavLink>
              </>
            )}
          </nav>
        </div>

        <div className="topbar-auth">
          {user ? (
            <div className="auth-user-controls">
              <div
                className={`auth-role-pill ${user.role}`}
                title={`Signed in as ${user.name} (${user.email})`}
              >
                {user.role === "recruiter" ? (
                  <BriefcaseBusiness size={14} />
                ) : (
                  <GraduationCap size={14} />
                )}
                <span className="auth-role-name">
                  <strong>{user.role === "recruiter" ? "Recruiter" : "Student"}:</strong> {user.name.split(" ")[0]}
                  <span className="demo-badge">DEMO DATA</span>
                </span>
              </div>

              <button
                type="button"
                className="auth-icon-button"
                onClick={openLoginModal}
                title="Switch Profile / Change Account"
              >
                <Repeat size={14} />
                <span>Switch</span>
              </button>

              <button
                type="button"
                className="auth-icon-button logout"
                onClick={logout}
                title="Sign Out"
              >
                <LogOut size={14} />
                <span>Logout</span>
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="button button-light auth-signin-btn"
              onClick={openLoginModal}
            >
              <LogIn size={15} />
              <span>Sign In</span>
            </button>
          )}
        </div>
      </header>

      {generationJob && (generationJob.status === "queued" || generationJob.status === "running") && location.pathname !== "/" && (
        <div className="generation-notice" role="status" aria-live="polite">
          <span>{pollingPaused ? "Passport status check paused" : `Passport generation continues • ${generationJob.step === "Queued" ? "Preparing your evidence…" : generationJob.step}`}</span>
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
          {/* Accessible to both Student and Recruiter */}
          <Route
            path="/"
            element={
              <CreatePassport
                job={generationJob}
                pollingPaused={pollingPaused}
                generationError={generationError}
                onRetryPolling={() => { setGenerationError(""); setPollingPaused(false); }}
                onJobChange={(job) => {
                  api.rememberJob(job);
                  setGenerationJob(job);
                  setGenerationError("");
                  setPollingPaused(false);
                  setDismissedCompletionId("");
                }}
              />
            }
          />
          <Route path="/student" element={<LatestPassportRedirect view="passport" />} />
          <Route path="/passport/:id" element={<StudentPassport />} />
          <Route path="/passport/:id/summary" element={<StudentSummary />} />

          {/* Recruiter-only protected routes */}
          <Route
            path="/recruiter"
            element={
              <ProtectedRoute>
                <LatestPassportRedirect view="recruiter" />
              </ProtectedRoute>
            }
          />
          <Route
            path="/recruiter/:id"
            element={
              <ProtectedRoute>
                <RecruiterView />
              </ProtectedRoute>
            }
          />
          <Route
            path="/recruiter/:id/evidence"
            element={
              <ProtectedRoute>
                <RecruiterEvidence />
              </ProtectedRoute>
            }
          />
          <Route
            path="/placement"
            element={
              <ProtectedRoute>
                <Suspense fallback={<div className="page-message">Loading placement insights…</div>}>
                  <PlacementCell />
                </Suspense>
              </ProtectedRoute>
            }
          />
          <Route path="*" element={<NotFound />} />
        </Routes>
      </main>

      {/* Global Interactive Login Modal */}
      <LoginModal />

      <footer>
        <BadgeCheck size={16} /> Built on evidence, not self-reported scores
        <span><BriefcaseBusiness size={15} /> Recruiter-ready</span>
        <span><BookOpenCheck size={15} /> Growth through proof</span>
        <LayoutDashboard size={15} />
      </footer>
    </div>
  );
}

function App() {
  return (
    <AuthProvider>
      <AppContent />
    </AuthProvider>
  );
}

export default App;
