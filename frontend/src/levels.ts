import type { PassportSkill } from "./api";

type Level = PassportSkill["level"];

/** Display names: "Claimed" is shown as "Unverified". */
export const LEVEL_LABELS: Record<Level, string> = {
  Claimed: "Unverified",
  Used: "Used",
  Applied: "Applied",
  Demonstrated: "Demonstrated",
};

/** Student-facing definitions (the Evidence Ladder legend). Evidence of use, never ability. */
export const LEVEL_HELP_STUDENT: Record<Level, string> = {
  Claimed: "Mentioned on your resume or a certificate; no evidence in the analysed files.",
  Used: "Listed in a dependency or config file you committed to.",
  Applied: "Used in source code you committed to (exact lines cited).",
  Demonstrated: "Applied, and exercised by tests that target that code.",
};

/** Neutral definitions for tooltips seen by anyone. */
export const LEVEL_HELP: Record<Level, string> = {
  Claimed: "Mentioned on the resume or a certificate; no evidence in the analysed files.",
  Used: "Listed in a dependency or config file the candidate committed to.",
  Applied: "Used in source code the candidate committed to (exact lines cited).",
  Demonstrated: "Applied, and exercised by tests that target that code.",
};

export const LEVEL_NOTE = "Levels show evidence of use in your code, not how well the skill is used.";
