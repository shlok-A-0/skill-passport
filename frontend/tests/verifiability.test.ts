import assert from "node:assert/strict";
import { test } from "node:test";
import { latestPerStudent } from "../src/latestPerStudent.ts";
import { excludedCitations, excludedCitationsText, notWrittenText, verifiability } from "../src/verifiability.ts";

const ok = (id: string) => ({ id, label: id, status: "ok", detail: "" });

// One counted code citation whose remaining signals all pass.
function passport(rejected_claims: object[]) {
  return {
    skills: [{
      skill_id: "python",
      level: "Applied",
      evidence: [{
        type: "code", repo: "demo", file_path: "app.py", start_line: 1, end_line: 9,
        integrity: [ok("line_share"), ok("commit_share")],
      }],
    }],
    rejected_claims,
  } as never;
}

const notWritten = (file_path: string) => ({
  claim: { skill_id: "flask", repo: "demo", file_path, start_line: 1, end_line: 5 },
  reason: "None of the cited lines were last changed in your commits.",
  kind: "not_written_by_candidate",
});

test("citations not written by the candidate force the tier to Low", () => {
  const result = verifiability(passport([notWritten("a.py"), notWritten("b.py"), notWritten("c.py")]));
  assert.equal(result.tier, "Low");
  assert.equal(result.notWritten, 3);
  assert.equal(result.earlierSources, 0);
  assert.deepEqual([result.passed, result.checked], [2, 2]); // the remaining signals are clean
});

test("a passport with no exclusions and clean signals is High", () => {
  const result = verifiability(passport([]));
  assert.equal(result.tier, "High");
  assert.equal(result.notWritten, 0);
  assert.equal(result.earlierSources, 0);
});

test("excluded citations always need a human look, even with no advisory warnings", () => {
  const result = verifiability(passport([notWritten("a.py"), notWritten("b.py"), notWritten("c.py")]));
  assert.equal(result.warnings.length, 0);
  assert.equal(excludedCitations(result), 3);
  assert.equal(excludedCitationsText(3), "3 excluded citations to review");
  assert.equal(excludedCitations(verifiability(passport([]))), 0);
});

test("notWrittenText is singular for one citation and plural otherwise", () => {
  assert.equal(notWrittenText(1), "1 citation not written by the candidate");
  assert.equal(notWrittenText(3), "3 citations not written by the candidate");
});

test("latest passport per student uses creation time, not a re-check update", () => {
  const passports = [
    { id: "latest", github_username: "student", created_at: "2026-10-20", updated_at: "2026-10-20" },
    { id: "old-rechecked", github_username: "STUDENT", created_at: "2026-10-01", updated_at: "2026-10-21" },
  ];

  assert.deepEqual(latestPerStudent(passports).map((passport) => passport.id), ["latest"]);
});
