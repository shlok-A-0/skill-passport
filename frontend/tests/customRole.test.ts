import assert from "node:assert/strict";
import test from "node:test";
import { droppedSummary, effectiveTargetRole, EXACT_ONLY_NOTE, extractionNote, JOB_DESCRIPTION_OPTION, jobDescriptionInputError } from "../src/customRole.ts";

test("the exact-matching note shows only when the AI was unavailable", () => {
  assert.equal(extractionNote({ mode: "exact_only" }), "AI suggestions unavailable; showing requirements found by exact matching.");
  assert.equal(extractionNote({ mode: "exact_only" }), EXACT_ONLY_NOTE);
  assert.equal(extractionNote({ mode: "ai" }), null);
  assert.equal(extractionNote({}), null);
});

test("a pasted job description submits the extracted custom role id only", () => {
  assert.equal(effectiveTargetRole("backend-intern", null), "backend-intern");
  assert.equal(effectiveTargetRole(JOB_DESCRIPTION_OPTION, null), "");
  assert.equal(effectiveTargetRole(JOB_DESCRIPTION_OPTION, "custom-1"), "custom-1");
});

test("job description input lengths are checked before extraction", () => {
  const text = "Python is required. ".repeat(3);
  assert.equal(jobDescriptionInputError("", text), "Enter the job title.");
  assert.match(jobDescriptionInputError("x".repeat(121), text) ?? "", /120/);
  assert.match(jobDescriptionInputError("Intern", "Python") ?? "", /at least 50/);
  assert.match(jobDescriptionInputError("Intern", "x".repeat(8001)) ?? "", /8,000/);
  assert.equal(jobDescriptionInputError("Intern", text), null);
});

test("dropped phrases are summarised", () => {
  assert.equal(droppedSummary(0), null);
  assert.equal(droppedSummary(1), "1 phrase could not be mapped");
  assert.equal(droppedSummary(3), "3 phrases could not be mapped");
});
