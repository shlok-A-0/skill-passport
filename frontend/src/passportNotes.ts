import type { Passport } from "./api";

/** Shown instead of an empty area when part of a passport was not generated or recorded. */
export const GROWTH_UNAVAILABLE = "Growth plan unavailable for this passport. Use Re-check to try again.";
export const KIT_UNAVAILABLE = "Interview questions unavailable.";
export const COVERAGE_MISSING = "Coverage not recorded. Re-check to update.";

const generationFailed = (passport: Passport) => Boolean(passport.generation_error || passport.generation_warning);

/** No growth tasks although generation failed or the role has gaps. */
export function growthUnavailable(passport: Passport): boolean {
  return !passport.growth_tasks.length
    && (generationFailed(passport) || passport.role_match.met < passport.role_match.total);
}

/** No interview questions although generation failed or there are code-backed skills to ask about. */
export function kitUnavailable(passport: Passport): boolean {
  return !passport.interview_kit.length
    && (generationFailed(passport) || passport.skills.some((skill) => skill.level === "Applied" || skill.level === "Demonstrated"));
}
