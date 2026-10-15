import type { IntegritySignal, Passport } from "./api";

export type Verifiability = {
  tier: "High" | "Medium" | "Low" | "Not checked";
  passed: number;
  checked: number;
  warnings: { signal: IntegritySignal; where: string }[];
  codeSkills: number;
  copiedExcluded: number;
};

const levelOrder: Record<string, number> = { Claimed: 0, Used: 1, Applied: 2, Demonstrated: 3 };
// Signals that point at the code itself not being the student's are weighted as serious.
const SERIOUS = new Set(["ai_leftover", "copy_match", "line_share"]);
export const tierOrder: Record<Verifiability["tier"], number> = { High: 3, Medium: 2, Low: 1, "Not checked": 0 };

/** Summarise authorship signals across a passport. Each file's signal is counted once. */
export function verifiability(passport: Passport): Verifiability {
  const seen = new Map<string, { signal: IntegritySignal; where: string }>();
  let codeSkills = 0;
  for (const skill of passport.skills) {
    const code = skill.evidence.filter((item) => item.type === "code");
    if (code.length && skill.level !== "Claimed") codeSkills += 1;
    for (const item of code) {
      for (const signal of item.integrity ?? []) {
        const scope = signal.id === "repo_history" ? item.repo
          : signal.id === "line_share" || signal.id === "copy_match" ? `${item.repo}/${item.file_path}#L${item.start_line}-L${item.end_line}`
          : `${item.repo}/${item.file_path}`;
        seen.set(`${signal.id}:${scope}`, { signal, where: scope });
      }
    }
  }
  const all = [...seen.values()].filter(({ signal }) => signal.status !== "unknown");
  const warnings = all.filter(({ signal }) => signal.status === "warn");
  const passed = all.length - warnings.length;
  // Citations excluded because the same code was confirmed in an older repository.
  const copiedExcluded = passport.rejected_claims.filter((item) => item.kind === "copied").length;
  let tier: Verifiability["tier"] = "Not checked";
  if (all.length) {
    const serious = warnings.some(({ signal }) => SERIOUS.has(signal.id));
    if (!warnings.length) tier = "High";
    else if (serious || warnings.length / all.length > 0.25) tier = "Low";
    else tier = "Medium";
  }
  if (copiedExcluded) tier = "Low";
  return { tier, passed, checked: all.length, warnings, codeSkills, copiedExcluded };
}

type Requirement = { skill_id: string; min_level: string; importance: string };

export function requirementsMet(passport: Passport, requirements: Requirement[]) {
  const met = requirements.filter((requirement) => {
    const skill = passport.skills.find((item) => item.skill_id === requirement.skill_id);
    return Boolean(
      skill && skill.level !== "Claimed" && skill.evidence.length
      && levelOrder[skill.level] >= levelOrder[requirement.min_level],
    );
  });
  return {
    met: met.length,
    mustMet: met.filter((item) => item.importance === "must").length,
    mustTotal: requirements.filter((item) => item.importance === "must").length,
  };
}

/**
 * Deterministic ranking: must-have requirements met, then all requirements met,
 * then verifiability tier, then fewer authorship warnings, then more code-backed skills.
 */
/** One passport per student: the most recently updated one for each GitHub username. */
export function latestPerStudent(passports: Passport[]): Passport[] {
  const latest = new Map<string, Passport>();
  const stamp = (passport: Passport) => passport.updated_at ?? passport.created_at;
  for (const passport of passports) {
    const key = passport.github_username.toLowerCase();
    const current = latest.get(key);
    if (!current || stamp(passport) > stamp(current)) latest.set(key, passport);
  }
  return [...latest.values()];
}

export function rankCandidates(passports: Passport[], requirements: Requirement[]) {
  return latestPerStudent(passports)
    .map((passport) => ({ passport, ...requirementsMet(passport, requirements), verify: verifiability(passport) }))
    .sort((a, b) =>
      b.mustMet - a.mustMet
      || b.met - a.met
      || tierOrder[b.verify.tier] - tierOrder[a.verify.tier]
      || a.verify.warnings.length - b.verify.warnings.length
      || b.verify.codeSkills - a.verify.codeSkills
      || a.passport.github_username.localeCompare(b.passport.github_username));
}
