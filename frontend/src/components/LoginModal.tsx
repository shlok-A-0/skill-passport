import React, { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  BriefcaseBusiness,
  CheckCircle2,
  Eye,
  EyeOff,
  GraduationCap,
  KeyRound,
  Lock,
  Mail,
  ShieldCheck,
  Sparkles,
  X,
} from "lucide-react";
import { DEMO_PROFILES, useAuth, type UserRole } from "../auth";

export default function LoginModal() {
  const { user, isLoginModalOpen, closeLoginModal, login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [lastAutoFilledRole, setLastAutoFilledRole] = useState<UserRole | null>(null);
  const [isPulsing, setIsPulsing] = useState(false);

  const emailInputRef = useRef<HTMLInputElement>(null);
  const submitButtonRef = useRef<HTMLButtonElement>(null);

  // When modal opens, initialize empty or with current user's email
  useEffect(() => {
    if (isLoginModalOpen) {
      if (user) {
        setEmail(user.email);
        setPassword(user.role === "recruiter" ? "recruiter2026" : "student2026");
      } else {
        setEmail("");
        setPassword("");
      }
      setErrorMessage("");
      setLastAutoFilledRole(null);
    }
  }, [isLoginModalOpen, user]);

  if (!isLoginModalOpen) return null;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMessage("");

    const res = login(email, password);
    if (!res.success) {
      setErrorMessage(res.error || "Authentication failed. Please verify your credentials.");
    }
  };

  const handleAutoFill = (role: UserRole) => {
    const profile = DEMO_PROFILES[role];
    setEmail(profile.user.email);
    setPassword(profile.passwordHint);
    setLastAutoFilledRole(role);
    setErrorMessage("");

    // Trigger visual pulse effect on input fields
    setIsPulsing(true);
    setTimeout(() => setIsPulsing(false), 900);

    // Focus the submit/enter button so user can press Enter immediately
    setTimeout(() => {
      submitButtonRef.current?.focus();
    }, 100);
  };

  return (
    <div className="auth-modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="auth-modal-title">
      <div className="auth-modal-card">
        {user && (
          <button
            type="button"
            className="auth-modal-close"
            onClick={closeLoginModal}
            aria-label="Close login modal"
          >
            <X size={18} />
          </button>
        )}

        <div className="auth-modal-header">
          <div className="auth-modal-brand-badge">
            <ShieldCheck size={28} />
          </div>
          <span className="eyebrow">PROOF, NOT CLAIMS</span>
          <h2 id="auth-modal-title">Demo Sign-In</h2>
          <p className="auth-modal-subtitle">
            This demo switches between sample Student and Recruiter views. It does not authenticate real accounts or secure backend data.
          </p>
        </div>

        {errorMessage && (
          <div className="notice notice-error auth-modal-alert" role="alert">
            {errorMessage}
          </div>
        )}

        {lastAutoFilledRole && (
          <div className="auth-autofill-hint" role="status">
            <CheckCircle2 size={15} />
            <span>
              Auto-filled <strong>{lastAutoFilledRole === "recruiter" ? "Recruiter" : "Student"}</strong> credentials! Press <strong>Sign In</strong> below to continue.
            </span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="auth-form">
          <div className={`auth-field ${isPulsing ? "auth-field-pulse" : ""}`}>
            <label htmlFor="auth-email">Email or Username</label>
            <div className="auth-input-wrapper">
              <Mail className="auth-input-icon" size={17} />
              <input
                id="auth-email"
                ref={emailInputRef}
                type="text"
                placeholder="e.g. recruiter@techcorp.com or student@university.edu"
                value={email}
                onChange={(e) => {
                  setEmail(e.target.value);
                  setErrorMessage("");
                }}
                autoComplete="username"
                required
              />
            </div>
          </div>

          <div className={`auth-field ${isPulsing ? "auth-field-pulse" : ""}`}>
            <div className="auth-field-header">
              <label htmlFor="auth-password">Password</label>
            </div>
            <div className="auth-input-wrapper">
              <Lock className="auth-input-icon" size={17} />
              <input
                id="auth-password"
                type={showPassword ? "text" : "password"}
                placeholder="Enter password"
                value={password}
                onChange={(e) => {
                  setPassword(e.target.value);
                  setErrorMessage("");
                }}
                autoComplete="current-password"
                required
              />
              <button
                type="button"
                className="auth-password-toggle"
                onClick={() => setShowPassword(!showPassword)}
                title={showPassword ? "Hide password" : "Show password"}
                tabIndex={-1}
              >
                {showPassword ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </div>

          <button
            ref={submitButtonRef}
            type="submit"
            className="button auth-submit-button"
            id="auth-submit-btn"
          >
            <span>Sign In / Enter</span>
            <ArrowRight size={16} />
          </button>
        </form>

        <div className="auth-divider">
          <span>Or Quick-Fill Demo Profiles</span>
        </div>

        <div className="auth-demo-options">
          {/* Recruiter demo option */}
          <button
            type="button"
            className={`auth-demo-card ${lastAutoFilledRole === "recruiter" ? "auth-demo-card-selected" : ""}`}
            onClick={() => handleAutoFill("recruiter")}
            id="demo-fill-recruiter"
          >
            <div className="auth-demo-card-head">
              <div className="auth-demo-icon recruiter">
                <BriefcaseBusiness size={18} />
              </div>
              <div className="auth-demo-info">
                <div className="auth-demo-title-row">
                  <span className="auth-demo-title">Recruiter Profile</span>
                  <span className="auth-demo-badge full-access">Full Access</span>
                </div>
                <span className="auth-demo-meta">Upload resumes & screen students</span>
              </div>
            </div>
            <p className="auth-demo-desc">
              Unrestricted access to all sections: Create, Student, Recruiter Evidence, and Placement Cell.
            </p>
            <div className="auth-demo-fill-action">
              <Sparkles size={13} />
              <span>Click to auto-fill recruiter credentials</span>
            </div>
          </button>

          {/* Student demo option */}
          <button
            type="button"
            className={`auth-demo-card ${lastAutoFilledRole === "student" ? "auth-demo-card-selected" : ""}`}
            onClick={() => handleAutoFill("student")}
            id="demo-fill-student"
          >
            <div className="auth-demo-card-head">
              <div className="auth-demo-icon student">
                <GraduationCap size={18} />
              </div>
              <div className="auth-demo-info">
                <div className="auth-demo-title-row">
                  <span className="auth-demo-title">Student Profile</span>
                  <span className="auth-demo-badge student-access">Candidate Access</span>
                </div>
                <span className="auth-demo-meta">Generate & view skill passport</span>
              </div>
            </div>
            <p className="auth-demo-desc">
              Access to Create Passport and Student Verification views. Recruiter views remain protected.
            </p>
            <div className="auth-demo-fill-action">
              <Sparkles size={13} />
              <span>Click to auto-fill student credentials</span>
            </div>
          </button>
        </div>

        <div className="auth-footer-note">
          <KeyRound size={13} />
          <span>Clicking either profile populates credentials directly so you can test instantly.</span>
        </div>
      </div>
    </div>
  );
}
