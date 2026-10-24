import type { InterviewAnchor, InterviewSession } from "./api";

export const OUTCOME_ORDER = ["explained_well", "partly_explained", "could_not_explain"] as const;

export const INTERVIEW_DISCLAIMER =
  "Outcomes are your observations. They never change evidence levels, role match or depth scores, and a weak answer is not proof of copying, AI use or a missing skill.";

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
