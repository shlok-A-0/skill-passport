import { BadgeCheck, BookOpenCheck, BriefcaseBusiness, LayoutDashboard, ShieldCheck } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import { Link, Navigate, NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
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
    api.passports()
      .then((passports) => setPassportId(passports[0]?.id ?? ""))
      .catch((reason: Error) => setError(reason.message))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <div className="page-message">Loading latest passport…</div>;
  if (error) return <div className="notice notice-error">{error}</div>;
  if (!passportId) {
    return <div className="page-message">No passports yet. <Link to="/">Create one first.</Link></div>;
  }
  return <Navigate replace to={`/${view}/${passportId}`} />;
}

function App() {
  return (
    <div className="app-shell">
      <header className="topbar">
        <Link className="brand" to="/"><span className="brand-mark"><ShieldCheck size={20} /></span><span>Skill Passport<small>PROOF, NOT CLAIMS</small></span></Link>
        <nav>
          <NavLink to="/">Create</NavLink>
          <NavLink to="/student">Student</NavLink>
          <NavLink to="/recruiter">Recruiter</NavLink>
          <NavLink to="/placement">Placement Cell</NavLink>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<CreatePassport />} />
          <Route path="/student" element={<LatestPassportRedirect view="passport" />} />
          <Route path="/recruiter" element={<LatestPassportRedirect view="recruiter" />} />
          <Route path="/passport/:id" element={<StudentPassport />} />
          <Route path="/recruiter/:id" element={<RecruiterView />} />
          <Route path="/recruiter/:id/evidence" element={<RecruiterEvidence />} />
          <Route path="/placement" element={<Suspense fallback={<div className="page-message">Loading placement insights…</div>}><PlacementCell /></Suspense>} />
          <Route path="*" element={<CreatePassport />} />
        </Routes>
      </main>
      <footer><BadgeCheck size={16} /> Built on evidence, not self-reported scores <span><BriefcaseBusiness size={15} /> Recruiter-ready</span><span><BookOpenCheck size={15} /> Growth through proof</span><LayoutDashboard size={15} /></footer>
    </div>
  );
}

export default App;
