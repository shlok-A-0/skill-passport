import { shouldRememberGenerationJob } from "./jobPolling.ts";

// `?.` so the module also loads under Node's test runner, where Vite's import.meta.env is absent.
export const API_BASE = import.meta.env?.VITE_API_BASE_URL ?? "http://localhost:8000";

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
  /** CI workflow evidence: whether GitHub recorded a run. Context only, not a check. */
  ci_run?: { verified: boolean; status: "passed" | "failed" | "in_progress" | "none" };
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
  reviewer_id?: string | null;
};

export type PassportPage = { items: Passport[]; total: number; limit: number; offset: number };

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
  /** Review endpoints, when interview outcomes moved the level: the level from the code checks. */
  evidence_level?: string;
  interview?: InterviewAdjustment;
};

/** How recorded interview outcomes moved one skill's level (recruiter views only). */
export type InterviewAdjustment = {
  outcome: InterviewOutcome;
  outcome_label: string;
  steps: number;
  evidence_level: string;
  level: string;
  assessed_at: string;
  assessor_id: string;
  questions: number;
};

export type Passport = {
  id: string;
  created_at: string;
  github_username: string;
  target_role: string;
  sources: { repos: number; resume: boolean; certificates: number; reports?: number };
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
  /** The student practice kit (I7). `good_answer_mentions` is returned on review endpoints only. */
  interview_kit: {
    skill_id: string;
    questions: { q: string; good_answer_mentions?: string }[];
  }[];
  is_demo: boolean;
  /** Review endpoints: skills whose level interview outcomes moved, and the evidence-only match. */
  interview_adjustments?: (InterviewAdjustment & { skill_id: string; name: string })[];
  evidence_role_match?: Passport["role_match"];
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
  /** Review endpoints only: account ownership and resume link checks. */
  identity?: IntegritySignal[];
  /** Present when the target role was built from a pasted job description. */
  custom_role?: { id: string; name: string; title: string; requirements: CustomRoleRequirement[] };
  reports?: ProjectReport[];
  /** Never fetched; ownership is only recognised for <username>.github.io. */
  portfolio?: Portfolio;
  /** Absent on passports built before the feature or while it is off; null when it couldn't be computed. */
  sql_depth?: SqlDepth | null;
  /** Skill-specific, cited evidence summaries; never a proficiency estimate. */
  skill_depth?: Record<string, SkillDepth | null>;
  /** The Codeforces handle supplied at create time (re-check reuses it), if any. */
  codeforces_handle?: string | null;
  /** Separate competitive-programming record; never affects skill levels or role matching. */
  competitive_programming?: CompetitiveProgramming | null;
};

export type SkillDepthEvidence = {
  repo: string;
  file_path: string;
  start_line: number;
  end_line: number;
  commit_sha: string;
  url: string;
  snippet: string;
  explanation?: string;
};
export type SkillDepthCompetency = {
  id: string;
  label: string;
  weight: number;
  points: number;
  status: "observed" | "not_observed" | "not_applicable" | "insufficient_evidence";
  explanation: string;
  evidence: SkillDepthEvidence[];
};
export type SkillDepth = {
  rubric_version: string;
  score: number | null;
  label: string;
  coverage: {
    percent: number;
    assessable_weight: number;
    applicable_weight: number;
    assessable_competencies: number;
    total_competencies: number;
    source_files_read?: number;
    source_files_total?: number;
    notes?: string[];
  };
  competencies: SkillDepthCompetency[];
  disclaimer: string;
};

/** One cited range of SQL (or the line that calls or tests it), linking to the exact GitHub lines. */
export type SqlEvidence = {
  repo: string;
  file_path: string;
  start_line: number;
  end_line: number;
  url: string;
  snippet: string;
  detail?: string;
  lines_written?: LinesWritten | null;
  /** Review endpoints only. */
  copy_check?: "no_match" | "advisory_match" | "not_checked";
  advisory?: { id: string; label: string; detail: string }[];
};
export type SqlDepthPartKey = "complexity" | "breadth" | "repetition" | "integration" | "authorship" | "testing";
export type SqlDepthPart = {
  label: string;
  value: number;
  weight: number;
  points: number;
  max_points: number;
  reason: string;
  evidence: SqlEvidence[];
};
export type SqlDepthQuery = {
  shape: string;
  statement: string;
  level: number;
  level_label: string;
  concepts: string[];
  tables: string[];
  occurrence_count: number;
  evidence: SqlEvidence[];
  lines_written: LinesWritten | null;
  wired: { status: "route" | "caller" | "loaded" | "none"; detail: string; evidence: SqlEvidence | null };
  test: { path: string; match: "name" | "import"; ci: "passed" | "not_recorded" } | null;
  correctness: "unverified";
};
/** SQL evidence depth: scored point by point from the analysed files; never changes a level. */
export type SqlDepth = {
  score: number;
  label: "Limited evidence" | "Basic" | "Developing" | "Solid" | "Strong";
  cap_reason: string | null;
  parts: Record<SqlDepthPartKey, SqlDepthPart>;
  concepts_found: { id: string; label: string; evidence: SqlEvidence[] }[];
  concepts_missing: { id: string; label: string }[];
  distinct_query_count: number;
  distinct_queries: SqlDepthQuery[];
  skipped: (SqlEvidence & { kind: string; reason: string })[];
  skipped_total: number;
  notes: string[];
  note: string;
  weights_note: string;
};

/** A requirement extracted from a job description: quote checked against the text, level set by fixed rules. */
export type CustomRoleRequirement = RoleRequirement & { quote: string; source: "exact" | "ai" };
export type CustomRole = {
  id: string;
  name: string;
  title: string;
  requirements: CustomRoleRequirement[];
  dropped: { skill_id: string | null; quote: string | null; reason: string }[];
  created_at: string;
  /** "exact_only" when the AI was unavailable and only exact skill-name matches were used. */
  mode?: "ai" | "exact_only";
  note?: string | null;
};
export type Features = {
  custom_roles: boolean;
  reports: boolean;
  sql_depth?: boolean;
  skill_depth?: boolean;
  skill_depth_profiles?: string[];
};

/** A skill a project report says was used, checked against code evidence. Never raises a level. */
export type ReportClaim = {
  skill_id: string;
  name?: string;
  quote: string;
  status: "corroborated" | "not_evidenced";
  evidence?: { type: Evidence["type"]; repo: string; file_path: string; start_line: number; end_line: number; url: string };
};
export type ProjectReport = {
  filename: string;
  claims: ReportClaim[];
  dropped: { skill_id: string | null; quote: string | null; reason: string }[];
  /** Student view: only links to the candidate's own repositories. */
  repo_links: { owner: string; repo: string; url: string; status: "own" | "other_account" }[];
  error?: string;
};

/** Competitive-programming record from the Codeforces public API. A separate axis:
 * it never feeds skill levels, role match or coverage. `ownership` is always
 * "unverified" (a typed handle proves nothing). Stats are computed over the
 * `submission_sample` most recent submissions, not lifetime history. */
export type CompetitiveProgramming = {
  handle: string;
  ownership: "unverified";
  data_source: "codeforces_public_api";
  fetched_at: string;
  /** Present only when a refresh failed and this is retained prior data. */
  stale?: boolean;
  /** Present only on failure payloads; stats are absent then. */
  error?: "not_found" | "unavailable";
  profile?: {
    rating: number | null;
    max_rating: number | null;
    rank: string | null;
    max_rank: string | null;
    contribution: number | null;
    registered_at: string | null;
  };
  rated_contests?: number;
  last_rated_contest_at?: string | null;
  /** Requested sample size (up to 200 most recent submissions). */
  submission_sample?: number;
  /** Submissions actually returned and examined; label stats with this count. */
  submissions_examined?: number;
  solved_count?: number;
  solved_max_problem_rating?: number | null;
  top_tags?: [string, number][];
  languages?: [string, number][];
};

export type Portfolio = { url: string; status: "own_github_pages" | "external_unverified" };

/** A one-time code the applicant publishes as a public gist to show they control the account. */
export type OwnershipChallenge = {
  challenge_id: string;
  github_username: string;
  code: string;
  expires_at: string;
  instructions: string;
};

export type InterviewOutcome = "explained_well" | "partly_explained" | "could_not_explain";
export type InterviewQuestionType = "explain" | "design_tradeoff" | "failure_case" | "modification" | "discussion";

/** The validated code a question is about, pinned to the commit the passport analysed. */
export type InterviewAnchor = {
  repo: string;
  repo_url: string | null;
  file_path: string;
  start_line: number;
  end_line: number;
  snippet_start_line: number;
  snippet_end_line: number;
  focus_start_line: number;
  focus_end_line: number;
  commit_sha: string | null;
  url: string | null;
  focus_url: string | null;
  snippet: string;
  snippet_sha256: string;
};

export type InterviewAssessment = {
  question_id: string;
  session_id: string;
  outcome: InterviewOutcome;
  note: string;
  assessor_id: string;
  assessed_at: string;
};

/** Recruiter-only: never part of the passport or any student response. */
export type InterviewQuestion = {
  id: string;
  skill_id: string;
  skill_name: string;
  requirement: { importance: "must" | "nice"; status: string; min_level: string; student_level: string | null; quote?: string | null } | null;
  selection_reasons: string[];
  question_type: InterviewQuestionType;
  type_label: string;
  question: string;
  follow_up: string | null;
  answer_points: string[];
  task: {
    statement: string;
    expected_behavior: string;
    acceptance_criteria: string[];
    suggested_tests: string[];
    evaluation_guidance: string;
  } | null;
  anchor: InterviewAnchor | null;
  related_url?: string | null;
  attribution_warnings: string[];
  stale: boolean;
  stale_reason: string | null;
  assessment: InterviewAssessment | null;
};

export type InterviewSummary = {
  total: number;
  assessed: number;
  unassessed: number;
  explained_well: number;
  partly_explained: number;
  could_not_explain: number;
  text: string;
};

export type InterviewSession = {
  id: string;
  passport_id: string;
  created_by: string;
  status: "generating" | "ready" | "failed";
  created_at: string;
  passport_changed: boolean;
  error: string | null;
  role_name: string | null;
  dropped: { reason: string; skill_id?: string }[];
  questions: InterviewQuestion[];
  summary: InterviewSummary;
  outcome_labels: Record<InterviewOutcome, string>;
};

export type RecruiterIdentity = { role: "recruiter"; recruiter_id: string; auth_required: boolean };
type MeResponse = RecruiterIdentity | { role: null; recruiter_id: null; auth_required: true };

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

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export function isMissingJobError(reason: unknown): boolean {
  return reason instanceof ApiError && reason.status === 404;
}

export function isAuthError(reason: unknown): boolean {
  return reason instanceof ApiError && reason.status === 401;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, options);
  } catch (reason) {
    if (reason instanceof Error && reason.name === "AbortError") {
      throw new ApiError("The request was interrupted. Please try again.", 0);
    }
    throw new ApiError("Can’t reach the Skill Passport server. Make sure the backend is running, then try again.", 0);
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = formatApiDetail(body.detail) ?? detail;
    } catch {
      // Keep the HTTP status when an error body is not JSON.
    }
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

function formatApiDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (!Array.isArray(detail)) return null;
  const messages = detail.map((item) => {
    if (!item || typeof item !== "object") return null;
    const issue = item as { loc?: unknown[]; msg?: unknown };
    if (typeof issue.msg !== "string") return null;
    const field = issue.loc?.[issue.loc.length - 1];
    const label = field === "resume" ? "Resume PDF" : field === "github_username" ? "GitHub username" : field === "target_role" ? "Target role" : null;
    if (label && /required|missing/i.test(issue.msg)) return `${label} is required.`;
    return issue.msg;
  }).filter((message): message is string => Boolean(message));
  return messages.length ? messages.join(" ") : null;
}

// The short-lived sign-in token, never the recruiter key or password. Kept for as long as the
// sign-in profile (localStorage); the server expires it after 8 hours and can revoke it.
const RECRUITER_SESSION = "recruiter-session";
let onAuthRequired: (() => void) | null = null;
// Used when browser storage is blocked: the session then lasts until the page is reloaded.
let memoryToken: string | null = null;

function recruiterToken(): string | null {
  try {
    return localStorage.getItem(RECRUITER_SESSION) ?? memoryToken;
  } catch {
    return memoryToken;
  }
}

function setRecruiterToken(token: string | null) {
  memoryToken = token;
  try {
    if (token) localStorage.setItem(RECRUITER_SESSION, token);
    else localStorage.removeItem(RECRUITER_SESSION);
  } catch {
    // storage unavailable: memoryToken keeps the session for this page
  }
}

/** Called when a recruiter request is rejected as unauthenticated (the app reopens sign-in). */
export function setAuthRequiredHandler(handler: (() => void) | null) {
  onAuthRequired = handler;
}

/** Review and interview endpoints. The server decides access; hiding pages is only convenience. */
async function reviewRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = recruiterToken();
  try {
    return await request<T>(path, {
      ...options,
      headers: { ...(options.headers ?? {}), ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    });
  } catch (reason) {
    if (isAuthError(reason)) {
      setRecruiterToken(null);
      onAuthRequired?.();
    }
    throw reason;
  }
}

const json = (body: unknown, extra: Record<string, string> = {}): RequestInit => ({
  headers: { "Content-Type": "application/json", ...extra },
  body: JSON.stringify(body),
});

export const api = {
  roles: () => request<Role[]>("/roles"),
  features: () => request<Features>("/features"),
  roleFromDescription: (title: string, description: string) =>
    request<CustomRole>("/roles/from-description", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, description }),
    }),
  ownershipChallenge: (github_username: string) =>
    request<OwnershipChallenge>("/ownership/challenge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ github_username }),
    }),
  createPassport: async (data: FormData) => {
    const created = await request<{ job_id: string; access_token: string }>("/passport/create", { method: "POST", body: data });
    sessionStorage.setItem(`passport-token:${created.job_id}`, created.access_token);
    sessionStorage.setItem(`passport-job-kind:${created.job_id}`, "generation");
    sessionStorage.setItem("passport-pending-job-id", created.job_id);
    api.rememberJob({ job_id: created.job_id, status: "queued", step: "Queued", completed_steps: [], error: null });
    return created;
  },
  pendingJobId: () => sessionStorage.getItem("passport-pending-job-id"),
  savedJob: (): PassportJob | null => {
    try {
      const raw = sessionStorage.getItem("passport-last-job");
      if (!raw) return null;
      const job = JSON.parse(raw) as PassportJob;
      return shouldRememberGenerationJob(
        job.job_id,
        sessionStorage.getItem("passport-pending-recheck"),
        sessionStorage.getItem(`passport-job-kind:${job.job_id}`),
      )
        ? job
        : null;
    } catch {
      sessionStorage.removeItem("passport-last-job");
      return null;
    }
  },
  rememberJob: (job: PassportJob) => sessionStorage.setItem("passport-last-job", JSON.stringify(job)),
  clearPendingJob: (id: string) => {
    if (sessionStorage.getItem("passport-pending-job-id") === id) {
      sessionStorage.removeItem("passport-pending-job-id");
    }
  },
  pendingRecheckId: (passportId: string) => {
    const pending = sessionStorage.getItem("passport-pending-recheck");
    if (!pending) return null;
    try {
      const parsed = JSON.parse(pending) as { job_id?: string; passport_id?: string };
      return parsed.passport_id === passportId ? parsed.job_id ?? null : null;
    } catch {
      sessionStorage.removeItem("passport-pending-recheck");
      return null;
    }
  },
  clearPendingRecheck: (id: string) => {
    const pending = sessionStorage.getItem("passport-pending-recheck");
    if (!pending) return;
    try {
      if ((JSON.parse(pending) as { job_id?: string }).job_id === id) {
        sessionStorage.removeItem("passport-pending-recheck");
      }
    } catch {
      sessionStorage.removeItem("passport-pending-recheck");
    }
  },
  hasPassportToken: (id: string) => Boolean(sessionStorage.getItem(`passport-token:${id}`)),
  latestPassportId: () => sessionStorage.getItem("passport-latest-id"),
  job: async (id: string) => {
    const job = await request<PassportJob>(`/jobs/${id}`, {
      headers: { "X-Passport-Token": sessionStorage.getItem(`passport-token:${id}`) ?? "" },
    });
    const isGenerationJob = shouldRememberGenerationJob(
      id,
      sessionStorage.getItem("passport-pending-recheck"),
      sessionStorage.getItem(`passport-job-kind:${id}`),
    );
    if (isGenerationJob) {
      api.rememberJob(job);
    } else if (job.status === "done" || job.status === "failed") {
      try {
        const saved = JSON.parse(sessionStorage.getItem("passport-last-job") ?? "null") as PassportJob | null;
        if (saved?.job_id === id) sessionStorage.removeItem("passport-last-job");
      } catch {
        sessionStorage.removeItem("passport-last-job");
      }
    }
    if (job.status === "done" && job.passport_id) sessionStorage.setItem("passport-latest-id", job.passport_id);
    if (job.status === "done" || job.status === "failed") api.clearPendingJob(id);
    if (job.status === "done" || job.status === "failed") api.clearPendingRecheck(id);
    if (job.status === "done" || job.status === "failed") sessionStorage.removeItem(`passport-job-kind:${id}`);
    return job;
  },
  passport: (id: string) => request<Passport>(`/passport/${id}`),
  reviewPassport: (id: string) => reviewRequest<Passport>(`/passport/${id}/review`),
  reviewPassports: (limit = 50, offset = 0) =>
    reviewRequest<PassportPage>(`/passports/review?limit=${limit}&offset=${offset}`),
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
  recheck: async (id: string) => {
    const token = sessionStorage.getItem(`passport-token:${id}`);
    if (!token) throw new Error("Re-check is only available in the browser session that created this passport.");
    const job = await request<{ job_id: string; access_token: string }>(
      `/passport/${id}/recheck`,
      { method: "POST", headers: { "X-Passport-Token": token } },
    );
    sessionStorage.setItem(`passport-token:${job.job_id}`, job.access_token);
    sessionStorage.setItem(`passport-job-kind:${job.job_id}`, "recheck");
    sessionStorage.setItem("passport-pending-recheck", JSON.stringify({ job_id: job.job_id, passport_id: id }));
    return job;
  },
  badge: (id: string) => request<Record<string, unknown>>(`/passport/${id}/badge`),
  recruiterLogin: async (key: string) => {
    const session = await request<{ token: string; recruiter_id: string; expires_at: string }>(
      "/auth/recruiter/login", { method: "POST", ...json({ key }) },
    );
    setRecruiterToken(session.token);
    return session;
  },
  hasRecruiterSession: () => Boolean(recruiterToken()),
  /** The signed-in recruiter, or null when nobody is (never redirects). */
  me: async (): Promise<RecruiterIdentity | null> => {
    const token = recruiterToken();
    const me = await request<MeResponse>("/auth/me", { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (me.role === null && token) setRecruiterToken(null);
    return me.role === "recruiter" ? me : null;
  },
  recruiterLogout: async () => {
    const token = recruiterToken();
    setRecruiterToken(null);
    if (token) await request("/auth/logout", { method: "POST", headers: { Authorization: `Bearer ${token}` } }).catch(() => undefined);
  },
  interviewSessions: (passportId: string) =>
    reviewRequest<{ items: InterviewSession[] }>(`/passport/${passportId}/interview-sessions`),
  /** The idempotency key makes a retried or double-submitted click return the same set. */
  createInterviewSession: (passportId: string, idempotencyKey: string) =>
    reviewRequest<InterviewSession>(`/passport/${passportId}/interview-sessions`, {
      method: "POST",
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  interviewSession: (sessionId: string) => reviewRequest<InterviewSession>(`/interview-sessions/${sessionId}`),
  assessInterviewQuestion: (sessionId: string, questionId: string, outcome: InterviewOutcome, note: string) =>
    reviewRequest<{ assessment: InterviewAssessment; summary: InterviewSummary }>(
      `/interview-sessions/${sessionId}/questions/${questionId}/assessment`,
      { method: "PUT", ...json({ outcome, note }) },
    ),
};
