import type { InterviewAdjustment, InterviewAnchor, InterviewSession } from "./api";

export const OUTCOME_ORDER = ["explained_well", "partly_explained", "could_not_explain"] as const;

export const INTERVIEW_DISCLAIMER =
  "Outcomes adjust this candidate's levels on recruiter pages (Explained well +1, Partly explained −1, Couldn't explain −2; the latest set decides, worst answer per skill). The evidence level from the code checks stays visible, and the student passport and credential are unchanged. A weak answer is not proof of copying, AI use or a missing skill.";

const LEVEL_RANK: Record<string, number> = { "Not found": -1, Claimed: 0, Used: 1, Applied: 2, Demonstrated: 3 };

/** "Interview −1 · evidence: Demonstrated" (the actual move, after caps), or "Interview: Explained well" when the level didn't move. */
export function adjustmentLabel(adjustment: InterviewAdjustment): string {
  const moved = (LEVEL_RANK[adjustment.level] ?? 0) - (LEVEL_RANK[adjustment.evidence_level] ?? 0);
  if (moved === 0) return `Interview: ${adjustment.outcome_label}`;
  return `Interview ${moved > 0 ? "+" : "−"}${Math.abs(moved)} · evidence: ${adjustment.evidence_level}`;
}

/** Lines of the cited snippet, numbered as on GitHub, with the question's focus marked. */
export function anchorLines(anchor: InterviewAnchor): { number: number; text: string; focus: boolean }[] {
  return anchor.snippet.split("\n").map((text, offset) => {
    const number = anchor.snippet_start_line + offset;
    return { number, text, focus: number >= anchor.focus_start_line && number <= anchor.focus_end_line };
  });
}

/** "Set 2 · 24 Oct, 14:02 · 3 of 5 explained well", newest set first in `sessions`. */
export function sessionLabel(session: InterviewSession, index: number, count: number): string {
  const when = new Date(session.created_at).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const state = session.status === "ready"
    ? `${session.summary.explained_well} of ${session.summary.total} explained well`
    : session.status === "failed" ? "generation failed" : "generating…";
  return `Set ${count - index} · ${when} · ${state}`;
}

/** The set to open: the newest ready one, else the newest. */
export function defaultSessionId(sessions: InterviewSession[]): string | null {
  return (sessions.find((item) => item.status === "ready") ?? sessions[0])?.id ?? null;
}

export function newIdempotencyKey(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}
