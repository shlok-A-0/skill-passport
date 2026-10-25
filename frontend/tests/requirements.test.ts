import assert from "node:assert/strict";
import { test } from "node:test";
import { countRequirementsMet } from "../src/requirements.ts";

const requirements = [
  { skill_id: "python", min_level: "Applied", importance: "must" },
  { skill_id: "git", min_level: "Used", importance: "must" },
  { skill_id: "docker", min_level: "Used", importance: "nice" },
  { skill_id: "unit-testing", min_level: "Applied", importance: "must" },
  { skill_id: "sql", min_level: "Used", importance: "nice" },
];
const evidence = [{}];

test("'with code evidence' counts only requirements met at Applied or Demonstrated", () => {
  const skills = [
    { skill_id: "python", level: "Demonstrated", evidence },
    { skill_id: "git", level: "Used", evidence }, // met, but from commit history only
    { skill_id: "docker", level: "Used", evidence }, // met, but from a Dockerfile only
    { skill_id: "unit-testing", level: "Used", evidence }, // below Applied: not met
    { skill_id: "sql", level: "Applied", evidence },
  ];
  assert.deepEqual(countRequirementsMet(skills, requirements), { met: 4, mustMet: 2, mustTotal: 3, codeMet: 2 });
});

test("a level raised by an interview meets the requirement but is not code evidence", () => {
  const skills = [
    { skill_id: "python", level: "Applied", evidence_level: "Used", evidence }, // dependency-only, raised by an interview
    { skill_id: "sql", level: "Used", evidence_level: "Applied", evidence }, // code-backed, lowered by an interview
  ];
  assert.deepEqual(countRequirementsMet(skills, requirements), { met: 2, mustMet: 1, mustTotal: 3, codeMet: 1 });
});

test("Unverified skills and skills without evidence never count", () => {
  const skills = [
    { skill_id: "python", level: "Claimed", evidence: [] },
    { skill_id: "git", level: "Used", evidence: [] },
  ];
  assert.deepEqual(countRequirementsMet(skills, requirements), { met: 0, mustMet: 0, mustTotal: 3, codeMet: 0 });
});
