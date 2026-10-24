import React from "react";
import { Link } from "react-router-dom";
import { ArrowLeft, BriefcaseBusiness, Lock, ShieldAlert, Sparkles } from "lucide-react";
import { useAuth, type UserRole } from "../auth";

interface ProtectedRouteProps {
  allowedRoles: UserRole[];
  children: React.ReactNode;
}

export default function ProtectedRoute({ allowedRoles, children }: ProtectedRouteProps) {
  const { user, isAuthenticated, switchRole, openLoginModal } = useAuth();

  if (!isAuthenticated) {
    return (
      <section className="access-denied-panel">
        <div className="access-denied-icon-wrap lock">
          <Lock size={36} />
        </div>
        <span className="eyebrow">AUTHENTICATION REQUIRED</span>
        <h1>Please Sign In</h1>
        <p>You must be signed in to view this section of Skill Passport.</p>
        <button
          type="button"
          className="button"
          onClick={openLoginModal}
        >
          Open Sign In Modal
        </button>
      </section>
    );
  }

  if (user && !allowedRoles.includes(user.role)) {
    return (
      <section className="access-denied-panel" role="alert">
        <div className="access-denied-icon-wrap alert">
          <ShieldAlert size={36} />
        </div>
        <span className="eyebrow">RESTRICTED ACCESS</span>
        <h1>Recruiter Privileges Required</h1>
        <p>
          You are currently signed in as <strong>{user.name} ({user.title})</strong> with candidate access.
          Recruiter reviews and Placement Cell insights are restricted to recruiter accounts.
        </p>

        <div className="access-denied-actions">
          <button
            type="button"
            className="button"
            onClick={() => switchRole("recruiter")}
          >
            <BriefcaseBusiness size={16} />
            <span>Switch to Recruiter Profile</span>
            <Sparkles size={14} />
          </button>

          <Link to="/student" className="button button-light">
            <ArrowLeft size={16} />
            <span>Return to Student Section</span>
          </Link>
        </div>
      </section>
    );
  }

  return <>{children}</>;
}
