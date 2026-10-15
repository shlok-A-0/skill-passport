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
  /** The test that targets this cited file (by name or import), which makes the skill Demonstrated. */
  demonstrated_by?: { path: string; match: "name" | "import" };
  /** A Dockerfile or CI workflow exists in the repo: context only, never a level change. */
  build_config_present?: boolean;
};

export type IntegritySignal = {
  id: string;
  label: string;
  status: "ok" | "warn" | "unknown";
  detail: string;
  matches?: { repo: string; url: string }[];
  earlier_source?: boolean;
  source_repo?: string;
  source_url?: string;
  source_first_seen?: string;
  candidate_first_seen?: string;
};

/** "copied" is the legacy name stored in older passports; both mean an earlier public source. */
export const EARLIER_SOURCE_KINDS = new Set(["earlier_source", "copied"]);

/**
 * "unsupported": failed a verification check. "earlier_source" (legacy "copied"): matches code
 * that appeared earlier elsewhere, so it isn't counted as the candidate's own work.
 * "review": the student-safe summary of excluded items.
 */
export type RejectedClaim = {
  claim?: Record<string, unknown>;
  failed_checks?: string[];
  reason: string;
  kind?: "unsupported" | "earlier_source" | "copied" | "review";
  url?: string;
  source_repo?: string;
  source_url?: string;
  source_first_seen?: string;
  candidate_first_seen?: string;
  count?: number;
  /** Student view only: the key to request a review with, and that request's status. */
  citation_key?: string;
  review_request?: { status: ReviewRequest["status"]; created_at: string } | null;
};

export type ReviewRequest = {
  id: string;
  passport_id: string;
  citation_key: string;
  note: string;
  created_at: string;
  status: "open" | "reviewed";
  reviewed_at: string | null;
};

/** Same format as the backend: "{skill_id}:{repo}:{file_path}:{start}-{end}". */
export function citationKey(claim: Record<string, unknown>): string {
  return `${String(claim.skill_id)}:${String(claim.repo)}:${String(claim.file_path)}:${String(claim.start_line)}-${String(claim.end_line)}`;
}

export type RepoCoverage = {
  name: string;
  source_files_total: number;
  source_files_read: number;
  lines_read: number;
  skipped: Record<string, number>;
  files_dropped_by_line_cap: number;
};

/** What was analysed: missing evidence means "not in these files", not "lacks the skill". */
export type Coverage = {
  repos_listed: number;
  repos_analysed: number;
  repos_skipped: { name: string; reason: string }[];
  source_files_total: number;
  source_files_read: number;
  lines_read: number;
  files_dropped_by_line_cap: number;
  repos: RepoCoverage[];
  notes: string[];
};

export const NOT_FOUND_TOOLTIP = "No evidence in the analysed files; this doesn't mean the candidate lacks the skill.";

/** Display name for a skill; older passports have no name, so fall back to the id. */
export function skillLabel(item: { skill_id: string; name?: string }): string {
  return item.name ?? item.skill_id.replace(/-/g, " ");
}

export type PassportSkill = {
  skill_id: string;
  name?: string;
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
  coverage?: Coverage;
  skills: PassportSkill[];
  rejected_claims: RejectedClaim[];
  role_match: {
    role_id: string;
    role_name: string;
    met: number;
    total: number;
    requirements: {
      skill_id: string;
      name?: string;
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
  generation_error?: string | null;
  /** Review endpoints only. */
  review_requests?: ReviewRequest[];
};

export type RoleRequirement = { skill_id: string; name?: string; min_level: string; importance: string };
export type Role = { id: string; name: string; requirements: RoleRequirement[] };
export type PassportJob = {
  job_id: string;
  status: "queued" | "running" | "done" | "failed";
  step: string;
  completed_steps: string[];
  error: string | null;
  passport_id?: string;
};

class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

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
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

// Kept in memory only (never localStorage), so it is gone when the tab closes.
let recruiterKey: string | null = null;

/** Review endpoints include fraud signals; the server may require a recruiter key. */
async function reviewRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const send = () => request<T>(path, {
    ...options,
    headers: { ...(options.headers ?? {}), ...(recruiterKey ? { "X-Recruiter-Key": recruiterKey } : {}) },
  });
  try {
    return await send();
  } catch (reason) {
    if (!(reason instanceof ApiError) || reason.status !== 401) throw reason;
    const entered = window.prompt("Enter the recruiter key to see the full review:");
    if (!entered) throw new Error("A recruiter key is required to open the review view.");
    recruiterKey = entered;
    try {
      return await send();
    } catch (retryReason) {
      if (retryReason instanceof ApiError && retryReason.status === 401) recruiterKey = null;
      throw retryReason;
    }
  }
}

export const api = {
  roles: () => request<Role[]>("/roles"),
  createPassport: (data: FormData) =>
    request<{ job_id: string }>("/passport/create", { method: "POST", body: data }),
  job: (id: string) => request<PassportJob>(`/jobs/${id}`),
  passport: (id: string) => request<Passport>(`/passport/${id}`),
  passports: () => request<Passport[]>("/passports"),
  reviewPassport: (id: string) => reviewRequest<Passport>(`/passport/${id}/review`),
  reviewPassports: () => reviewRequest<Passport[]>("/passports/review"),
  requestReview: (passportId: string, citation_key: string, note: string) =>
    request<ReviewRequest>(`/passport/${passportId}/review-requests`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ citation_key, note }),
    }),
  markReviewed: (requestId: string) =>
    reviewRequest<ReviewRequest>(`/review-requests/${requestId}/mark-reviewed`, { method: "POST" }),
  recheck: (id: string) =>
    request<{ passport: Passport; level_changes: Passport["level_changes"] }>(
      `/passport/${id}/recheck`,
      { method: "POST" },
    ),
  badge: (id: string) => request<Record<string, unknown>>(`/passport/${id}/badge`),
};
