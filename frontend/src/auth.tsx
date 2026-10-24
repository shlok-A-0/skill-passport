import React, { createContext, useContext, useEffect, useState } from "react";
import {
  type DemoProfile,
  DEMO_PROFILES,
  type User,
  type UserRole,
  canAccessRoute,
} from "./authTypes";

export type { DemoProfile, User, UserRole };
export { DEMO_PROFILES, canAccessRoute };

const STORAGE_KEY = "skill_passport_auth_session";

interface AuthContextType {
  user: User | null;
  role: UserRole | null;
  isAuthenticated: boolean;
  isLoginModalOpen: boolean;
  openLoginModal: () => void;
  closeLoginModal: () => void;
  login: (email: string, password: string) => { success: boolean; error?: string };
  quickLogin: (role: UserRole) => void;
  logout: () => void;
  switchRole: (role: UserRole) => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(() => {
    try {
      const saved = localStorage.getItem(STORAGE_KEY);
      if (saved) return JSON.parse(saved) as User;
    } catch {
      // ignore JSON parse error
    }
    return null;
  });

  const [isLoginModalOpen, setIsLoginModalOpen] = useState<boolean>(() => {
    // If no user is logged in initially, open the modal
    try {
      return !localStorage.getItem(STORAGE_KEY);
    } catch {
      return true;
    }
  });

  useEffect(() => {
    try {
      if (user) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(user));
      } else {
        localStorage.removeItem(STORAGE_KEY);
      }
    } catch {
      // storage unavailable
    }
  }, [user]);

  const login = (email: string, password: string): { success: boolean; error?: string } => {
    const trimmedEmail = email.trim().toLowerCase();
    const trimmedPass = password.trim();

    if (!trimmedEmail) {
      return { success: false, error: "Please enter an email or username." };
    }
    if (!trimmedPass) {
      return { success: false, error: "Please enter a password." };
    }

    // Check recruiter match
    if (
      trimmedEmail === DEMO_PROFILES.recruiter.user.email.toLowerCase() ||
      trimmedEmail.includes("recruiter")
    ) {
      const authenticatedUser = { ...DEMO_PROFILES.recruiter.user };
      if (!trimmedEmail.includes("@techcorp.com") && trimmedEmail.includes("@")) {
        authenticatedUser.email = trimmedEmail;
      }
      setUser(authenticatedUser);
      setIsLoginModalOpen(false);
      return { success: true };
    }

    // Check student match
    if (
      trimmedEmail === DEMO_PROFILES.student.user.email.toLowerCase() ||
      trimmedEmail.includes("student")
    ) {
      const authenticatedUser = { ...DEMO_PROFILES.student.user };
      if (!trimmedEmail.includes("@university.edu") && trimmedEmail.includes("@")) {
        authenticatedUser.email = trimmedEmail;
      }
      setUser(authenticatedUser);
      setIsLoginModalOpen(false);
      return { success: true };
    }

    // Default generic login defaults to student role
    const genericUser: User = {
      id: `usr-${Date.now()}`,
      name: trimmedEmail.split("@")[0].replace(/[._-]/g, " "),
      email: trimmedEmail,
      role: "student",
      title: "Student Candidate",
      organization: "Applicant",
    };
    setUser(genericUser);
    setIsLoginModalOpen(false);
    return { success: true };
  };

  const quickLogin = (selectedRole: UserRole) => {
    const profile = DEMO_PROFILES[selectedRole];
    setUser(profile.user);
    setIsLoginModalOpen(false);
  };

  const switchRole = (newRole: UserRole) => {
    const profile = DEMO_PROFILES[newRole];
    setUser(profile.user);
    setIsLoginModalOpen(false);
  };

  const logout = () => {
    setUser(null);
    setIsLoginModalOpen(true);
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        role: user?.role ?? null,
        isAuthenticated: !!user,
        isLoginModalOpen,
        openLoginModal: () => setIsLoginModalOpen(true),
        closeLoginModal: () => {
          if (user) setIsLoginModalOpen(false);
        },
        login,
        quickLogin,
        logout,
        switchRole,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}
