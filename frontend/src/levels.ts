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
  Claimed: "From your documents, uncommitted files or an earlier check. No current evidence in the analysed files.",
  Used: "Found in a dependency or config file you committed to.",
  Applied: "Used in code you committed to, with exact lines cited.",
  Demonstrated: "Applied, plus tests you wrote that check that code.",
};

/** Neutral definitions for tooltips seen by anyone. */
export const LEVEL_HELP: Record<Level, string> = {
  Claimed: "From documents, uncommitted files or an earlier check. No current evidence in the analysed files.",
  Used: "Found in a dependency or config file the candidate committed to.",
  Applied: "Used in code the candidate committed to, with exact lines cited.",
  Demonstrated: "Applied, plus tests the candidate wrote that check that code.",
};

export const LEVEL_NOTE = "Levels come from fixed evidence rules, never AI. They show evidence of use in the analysed code, not overall ability.";
