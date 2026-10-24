import type { SqlDepth, SqlDepthPart, SqlDepthPartKey, SqlEvidence } from "./api.ts";

export const SQL_DEPTH_NOTE = "Depth of SQL evidence in the analysed repositories, not a full measure of skill.";
export const SQL_DEPTH_MISSING = "SQL evidence depth not recorded for this passport. Re-check to update.";
export const SQL_DEPTH_UNAVAILABLE = "SQL evidence depth unavailable for this passport. Use Re-check to try again.";
export const CREATE_SCORE_NOTE = "No scores for general skills. SQL depth is scored point by point, with the proof for each point.";

/** The order the parts are weighted in: complexity, breadth, repetition, integration, authorship, testing. */
export const PART_ORDER: SqlDepthPartKey[] = ["complexity", "breadth", "repetition", "integration", "authorship", "testing"];

export function sqlDepthHeadline(result: Pick<SqlDepth, "score" | "label">): string {
  return `SQL evidence depth: ${result.score}/100 · ${result.label}`;
}

/** "16.7/20": points earned out of the most the part can give. */
export function partPoints(part: Pick<SqlDepthPart, "points" | "max_points">): string {
  const points = Number.isInteger(part.points) ? String(part.points) : part.points.toFixed(1);
  return `${points}/${part.max_points}`;
}

export function partRows(result: SqlDepth): (SqlDepthPart & { key: SqlDepthPartKey })[] {
  return PART_ORDER.filter((key) => result.parts?.[key]).map((key) => ({ key, ...result.parts[key] }));
}

/** "repo/path · L3–L9", or just "repo/path" when there are no line numbers. */
export function evidenceWhere(item: Pick<SqlEvidence, "repo" | "file_path" | "start_line" | "end_line">): string {
  const lines = item.start_line > 0
    ? ` · L${item.start_line}${item.end_line !== item.start_line ? `–L${item.end_line}` : ""}`
    : "";
  return `${item.repo}/${item.file_path}${lines}`;
}

/** Cap and limited-evidence warnings shown above the breakdown. */
export function sqlDepthWarnings(result: SqlDepth): string[] {
  return result.cap_reason ? [result.cap_reason] : [];
}
