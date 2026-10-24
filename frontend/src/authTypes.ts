export type UserRole = "student" | "recruiter";

export interface User {
  id: string;
  name: string;
  email: string;
  role: UserRole;
  title: string;
  organization: string;
}

export interface DemoProfile {
  user: User;
  passwordHint: string;
  badge: string;
  description: string;
}

export const DEMO_PROFILES: Record<UserRole, DemoProfile> = {
  recruiter: {
    user: {
      id: "recruiter-demo-01",
      name: "Alex Vance",
      email: "recruiter@techcorp.com",
      role: "recruiter",
      title: "Senior Technical Talent Lead",
      organization: "TechCorp Labs",
    },
    passwordHint: "recruiter2026",
    badge: "Full Access • All Sections",
    description: "Upload resumes, review verified GitHub evidence packets, and screen cohorts in Placement Cell.",
  },
  student: {
    user: {
      id: "student-demo-01",
      name: "Jordan Smith",
      email: "student@university.edu",
      role: "student",
      title: "Computer Science Candidate",
      organization: "Metropolitan University",
    },
    passwordHint: "student2026",
    badge: "Candidate Access • Create & Student View",
    description: "Build skill passports, view verified code citations, and submit verification review requests.",
  },
};

export function canAccessRoute(pathname: string, role: UserRole | null): boolean {
  if (pathname === "/" || pathname.startsWith("/passport") || pathname === "/student") {
    return true;
  }
  if (pathname.startsWith("/recruiter") || pathname.startsWith("/placement")) {
    return role === "recruiter";
  }
  return true;
}
