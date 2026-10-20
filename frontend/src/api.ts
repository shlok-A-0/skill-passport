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
  /** Code citations: cited lines git blame attributes to the candidate; null when unknown. */
  lines_written?: LinesWritten | null;
  integrity?: IntegritySignal[];
  test_file_group?: boolean;
  /** The test that targets this cited file (by name or import), which makes the skill Demonstrated. */
  demonstrated_by?: { path: string; match: "name" | "import" };
  /** A Dockerfile or CI workflow exists in the repo: context only, never a level change. */
  build_config_present?: boolean;
  /** Review endpoints and badge only. */
  record?: EvidenceRecord;
};

export type EvidenceRecord = {
  commit_sha: string;
  path: string;
  start_line: number;
  end_line: number;
  snippet_sha256: string;
  retrieved_at: string | null;
  verifier_version: string;
  checks: Record<string, boolean>;
  rules_applied: string[];
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

export type LinesWritten = { written: number; cited: number };

/** Same wording as the backend's CHECK_LABELS; used when a response doesn't include them. */
export const DEFAULT_CHECK_LABELS: Record<string, string> = {
  file_exists: "File exists in repository",
  lines_valid: "Cited lines are valid",
  authored: "You committed to this file",
  not_fork: "Repository is not a fork",
  signature_found: "Skill signature found in code",
  lines_written: "Cited lines written by you",
  skill_id: "Skill is in the skill framework",
};

/** "copied" is the legacy name stored in older passports; both mean an earlier public source. */
export const EARLIER_SOURCE_KINDS = new Set(["earlier_source", "copied"]);
export const NOT_WRITTEN_KIND = "not_written_by_candidate";
/** Citations removed after verification; students see each one and can request a review. */
export const EXCLUDED_KINDS = new Set([...EARLIER_SOURCE_KINDS, NOT_WRITTEN_KIND]);

/**
 * "unsupported": failed a verification check. "earlier_source" (legacy "copied"): matches code
 * that appeared earlier elsewhere, so it isn't counted as the candidate's own work.
 * "not_written_by_candidate": none of the cited lines were last changed in the candidate's commits.
 * "review": the student-safe summary of excluded items.
 */
export type RejectedClaim = {
  claim?: Record<string, unknown>;
  failed_checks?: string[];
  reason: string;
  kind?: "unsupported" | "earlier_source" | "copied" | "not_written_by_candidate" | "review";
  lines_written?: LinesWritten;
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

/** What supports a requirement, how clearly it traces to the candidate, what's uncertain, what to ask. */
export type RequirementPacket = {
  skill_id: string;
  status: "met" | "partial" | "missing";
  status_label: "Met" | "Partial" | "Not found";
  support: { type: Evidence["type"]; repo: string; file_path: string; start_line: number; end_line: number; url: string }[];
  attribution: "High" | "Medium" | "Low" | "Low (limited history)" | "Not applicable";
  uncertain: string[];
  ask_next: { question: string; good_answer_mentions?: string | null; source: "interview_kit" | "template" };
  summary: string;
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
  resume_mention?: string | null;
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
  /** Labels for the evidence checks, from the backend. */
  check_labels?: Record<string, string>;
  /** Review endpoints only. */
  review_requests?: ReviewRequest[];
  /** Review endpoints only: one reviewer-ready packet per role requirement. */
  requirement_packets?: RequirementPacket[];
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

export function isMissingJobError(reason: unknown): boolean {
  return reason instanceof ApiError && reason.status === 404;
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
  createPassport: async (data: FormData) => {
    const created = await request<{ job_id: string; access_token: string }>("/passport/create", { method: "POST", body: data });
    sessionStorage.setItem(`passport-token:${created.job_id}`, created.access_token);
    sessionStorage.setItem("passport-pending-job-id", created.job_id);
    return created;
  },
  pendingJobId: () => sessionStorage.getItem("passport-pending-job-id"),
  clearPendingJob: (id: string) => {
    if (sessionStorage.getItem("passport-pending-job-id") === id) {
      sessionStorage.removeItem("passport-pending-job-id");
    }
  },
  hasPassportToken: (id: string) => Boolean(sessionStorage.getItem(`passport-token:${id}`)),
  latestPassportId: () => sessionStorage.getItem("passport-latest-id"),
  job: async (id: string) => {
    const job = await request<PassportJob>(`/jobs/${id}`, {
      headers: { "X-Passport-Token": sessionStorage.getItem(`passport-token:${id}`) ?? "" },
    });
    if (job.status === "done" && job.passport_id) sessionStorage.setItem("passport-latest-id", job.passport_id);
    if (job.status === "done" || job.status === "failed") api.clearPendingJob(id);
    return job;
  },
  passport: (id: string) => request<Passport>(`/passport/${id}`),
  reviewPassport: (id: string) => reviewRequest<Passport>(`/passport/${id}/review`),
  reviewPassports: () => reviewRequest<Passport[]>("/passports/review"),
  requestReview: (passportId: string, citation_key: string, note: string) => {
    const token = sessionStorage.getItem(`passport-token:${passportId}`);
    if (!token) throw new Error("Review requests must be submitted from the browser session that created this passport.");
    return request<ReviewRequest>(`/passport/${passportId}/review-requests`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Passport-Token": token },
      body: JSON.stringify({ citation_key, note }),
    });
  },
  markReviewed: (requestId: string) =>
    reviewRequest<ReviewRequest>(`/review-requests/${requestId}/mark-reviewed`, { method: "POST" }),
  recheck: (id: string) => {
    const token = sessionStorage.getItem(`passport-token:${id}`);
    if (!token) throw new Error("Re-check is only available in the browser session that created this passport.");
    return request<{ passport: Passport; level_changes: Passport["level_changes"] }>(
      `/passport/${id}/recheck`,
      { method: "POST", headers: { "X-Passport-Token": token } },
    );
  },
  badge: (id: string) => request<Record<string, unknown>>(`/passport/${id}/badge`),
};
