import React, { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  BriefcaseBusiness,
  CheckCircle2,
  Eye,
  EyeOff,
  GraduationCap,
  Lock,
  Mail,
  ShieldCheck,
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
  const dialogRef = useRef<HTMLDivElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!isLoginModalOpen) return;
    previousFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    emailInputRef.current?.focus();
    const trapFocus = (event: KeyboardEvent) => {
      if (event.key !== "Tab" || !dialogRef.current) return;
      const focusable = [...dialogRef.current.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )];
      if (!focusable.length) {
        event.preventDefault();
        dialogRef.current.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && (document.activeElement === first || !dialogRef.current.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !dialogRef.current.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", trapFocus);
    return () => {
      document.removeEventListener("keydown", trapFocus);
      previousFocusRef.current?.focus();
    };
  }, [isLoginModalOpen]);

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
      <div className="auth-modal-card" ref={dialogRef} tabIndex={-1}>
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
          <h2 id="auth-modal-title">Demo sign-in</h2>
          <p className="auth-modal-subtitle">
            Switch between sample Student and Recruiter views. This does not authenticate real accounts or secure backend data.
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
              <strong>{lastAutoFilledRole === "recruiter" ? "Recruiter" : "Student"}</strong> credentials filled. Press <strong>Sign in</strong> to continue.
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
            <span>Sign in</span>
            <ArrowRight size={16} />
          </button>
        </form>

        <div className="auth-divider">
          <span>Or use a demo profile</span>
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
                  <span className="auth-demo-title">Recruiter</span>
                </div>
                <span className="auth-demo-meta">All views, including Placement Cell</span>
              </div>
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
                  <span className="auth-demo-title">Student</span>
                </div>
                <span className="auth-demo-meta">Create and view a passport</span>
              </div>
            </div>
          </button>
        </div>

      </div>
    </div>
  );
}
