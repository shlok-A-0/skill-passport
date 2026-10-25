import assert from "node:assert/strict";
import { test } from "node:test";
import { adjustmentLabel, anchorLines, defaultSessionId, sessionLabel } from "../src/interview.ts";

const adjustment = (outcome: string, steps: number, evidence_level: string, level: string) =>
  ({ outcome, outcome_label: outcome, steps, evidence_level, level } as never);

test("interview tags show the actual move after caps, and the evidence level", () => {
  assert.equal(adjustmentLabel(adjustment("Partly explained", -1, "Demonstrated", "Applied")), "Interview −1 · evidence: Demonstrated");
  assert.equal(adjustmentLabel(adjustment("Explained well", 1, "Used", "Applied")), "Interview +1 · evidence: Used");
  // Couldn't explain is -2, but Used can only drop one step to Claimed.
  assert.equal(adjustmentLabel(adjustment("Couldn't explain", -2, "Used", "Claimed")), "Interview −1 · evidence: Used");
  // Capped with no move (already Demonstrated): the outcome is still shown.
  assert.equal(adjustmentLabel(adjustment("Explained well", 1, "Demonstrated", "Demonstrated")), "Interview: Explained well");
});

const anchor = {
  snippet: "def a():\n    b()\n    return c",
  snippet_start_line: 10,
  focus_start_line: 11,
  focus_end_line: 12,
} as never;

const session = (id: string, status: "ready" | "failed" | "generating", well = 0, total = 4) => ({
  id, status, created_at: "2026-10-24T09:00:00Z",
  summary: { explained_well: well, total },
} as never);

test("anchor lines keep GitHub numbering and mark the question's focus", () => {
  assert.deepEqual(anchorLines(anchor), [
    { number: 10, text: "def a():", focus: false },
    { number: 11, text: "    b()", focus: true },
    { number: 12, text: "    return c", focus: true },
  ]);
});

test("the newest ready set opens by default; a failed newest set is skipped", () => {
  assert.equal(defaultSessionId([session("new", "failed"), session("old", "ready")]), "old");
  assert.equal(defaultSessionId([session("only", "failed")]), "only");
  assert.equal(defaultSessionId([]), null);
});

test("set labels count from the oldest and summarise saved outcomes only", () => {
  const label = sessionLabel(session("s", "ready", 3, 5), 0, 2);
  assert.match(label, /^Set 2 · /);
  assert.match(label, /3 of 5 explained well$/);
  assert.match(sessionLabel(session("f", "failed"), 1, 2), /^Set 1 · .* generation failed$/);
});
