/**
 * Requirement matching for the Placement Cell. No imports, so `node --test` can load it directly.
 */
export type SkillLike = { skill_id: string; level: string; evidence: unknown[] };
export type RequirementLike = { skill_id: string; min_level: string; importance: string };

const LEVEL_ORDER: Record<string, number> = { Claimed: 0, Used: 1, Applied: 2, Demonstrated: 3 };
/** Levels backed by code citations; "Used" comes only from dependency or config files and commits. */
export const CODE_LEVELS = new Set(["Applied", "Demonstrated"]);

function metBy(skills: SkillLike[], requirement: RequirementLike): SkillLike | undefined {
  const skill = skills.find((item) => item.skill_id === requirement.skill_id);
  const met = skill && skill.level !== "Claimed" && skill.evidence.length > 0
    && LEVEL_ORDER[skill.level] >= LEVEL_ORDER[requirement.min_level];
  return met ? skill : undefined;
}

/** Requirements met; `codeMet` counts only those met with code evidence (Applied or Demonstrated). */
export function countRequirementsMet(skills: SkillLike[], requirements: RequirementLike[]) {
  const met = requirements.filter((requirement) => metBy(skills, requirement));
  return {
    met: met.length,
    mustMet: met.filter((item) => item.importance === "must").length,
    mustTotal: requirements.filter((item) => item.importance === "must").length,
    codeMet: met.filter((item) => CODE_LEVELS.has(metBy(skills, item)!.level)).length,
  };
}
