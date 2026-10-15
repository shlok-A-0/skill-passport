export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

export type Evidence = {
  type: "code" | "dependency" | "config" | "commit";
  repo: string;
  file_path: string;
  start_line: number;
  end_line: number;
  snippet_start_line?: number;
  matched_line?: number;
  commit_sha: string;
  url: string;
  snippet: string;
  explanation: string;
  checks: Record<string, boolean>;
  integrity?: IntegritySignal[];
  test_file_group?: boolean;
};

export type IntegritySignal = {
  id: string;
  label: string;
  status: "ok" | "warn" | "unknown";
  detail: string;
  matches?: { repo: string; url: string }[];
  confirmed_copy?: boolean;
  source_repo?: string;
  source_url?: string;
  source_created_at?: string;
};

export type RejectedClaim = {
  claim: Record<string, unknown>;
  failed_checks: string[];
  reason: string;
  kind?: "unsupported" | "copied";
  source_url?: string;
};

export type PassportSkill = {
  skill_id: string;
  level: "Claimed" | "Used" | "Applied" | "Demonstrated";
  certified: boolean;
  evidence: Evidence[];
  resume_mention: string | null;
};

export type Passport = {
  id: string;
  created_at: string;
  github_username: string;
  target_role: string;
  sources: { repos: number; resume: boolean; certificates: number };
  skills: PassportSkill[];
  rejected_claims: RejectedClaim[];
  role_match: {
    role_id: string;
    role_name: string;
    met: number;
    total: number;
    requirements: {
      skill_id: string;
      importance: "must" | "nice";
      status: "met" | "partial" | "missing";
      student_level: string | null;
      min_level: string;
      reason: string;
    }[];
  };
  growth_tasks: {
    skill_id: string;
    target_repo: string;
    task: string;
    evidence_it_will_create: string;
  }[];
  interview_kit: {
    skill_id: string;
    questions: { q: string; good_answer_mentions: string }[];
  }[];
  is_demo: boolean;
  level_changes?: { skill_id: string; from: string; to: string }[];
  updated_at?: string;
  generation_warning?: string | null;
};

export type Role = { id: string; name: string; requirements: unknown[] };
export type PassportJob = {
  job_id: string;
  status: "queued" | "running" | "done" | "failed";
  step: string;
  completed_steps: string[];
  error: string | null;
  passport_id?: string;
};

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, options);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body.detail ?? detail;
    } catch {
      // Keep the HTTP status when an error body is not JSON.
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const api = {
  roles: () => request<Role[]>("/roles"),
  createPassport: (data: FormData) =>
    request<{ job_id: string }>("/passport/create", { method: "POST", body: data }),
  job: (id: string) => request<PassportJob>(`/jobs/${id}`),
  passport: (id: string) => request<Passport>(`/passport/${id}`),
  passports: () => request<Passport[]>("/passports"),
  recheck: (id: string) =>
    request<{ passport: Passport; level_changes: Passport["level_changes"] }>(
      `/passport/${id}/recheck`,
      { method: "POST" },
    ),
  badge: (id: string) => request<Record<string, unknown>>(`/passport/${id}/badge`),
};
