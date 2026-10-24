import assert from "node:assert/strict";
import { test } from "node:test";
import {
  evidenceWhere, partPoints, partRows, PART_ORDER, SQL_DEPTH_NOTE, sqlDepthHeadline, sqlDepthWarnings,
} from "../src/sqlDepth.ts";

const part = (label: string, points: number, max_points: number) => ({
  label, value: (points / max_points) * 100, weight: max_points / 100, points, max_points, reason: `${label} reason`, evidence: [],
});

const result = {
  score: 24,
  label: "Limited evidence",
  cap_reason: "Fewer than 3 distinct queries: shown as Limited evidence.",
  parts: {
    testing: part("Testing", 0, 10),
    complexity: part("Complexity", 5, 20),
    breadth: part("Breadth", 0, 20),
    repetition: part("Repetition", 4, 20),
    integration: part("Integration", 0, 15),
    authorship: part("Authorship", 15, 15),
  },
} as never;

test("the headline shows the score out of 100 and the label", () => {
  assert.equal(sqlDepthHeadline({ score: 72, label: "Solid" }), "SQL evidence depth: 72/100 · Solid");
});

test("parts are listed in weight order with points earned out of the maximum", () => {
  const rows = partRows(result);
  assert.deepEqual(rows.map((row) => row.key), PART_ORDER);
  assert.equal(partPoints(rows[0]), "5/20");
  assert.equal(partPoints({ points: 16.7, max_points: 20 }), "16.7/20");
});

test("parts missing from an older stored result are skipped, not invented", () => {
  const partial = { parts: { complexity: part("Complexity", 5, 20) } } as never;
  assert.deepEqual(partRows(partial).map((row) => row.key), ["complexity"]);
});

test("cap and limited-evidence warnings are shown", () => {
  assert.deepEqual(sqlDepthWarnings(result), ["Fewer than 3 distinct queries: shown as Limited evidence."]);
  assert.deepEqual(sqlDepthWarnings({ cap_reason: null } as never), []);
});

test("evidence locations name the repository, file and lines", () => {
  assert.equal(evidenceWhere({ repo: "api", file_path: "app/db.py", start_line: 3, end_line: 9 }), "api/app/db.py · L3–L9");
  assert.equal(evidenceWhere({ repo: "api", file_path: "app/db.py", start_line: 4, end_line: 4 }), "api/app/db.py · L4");
});

test("the disclaimer says it is not a full measure of skill", () => {
  assert.equal(SQL_DEPTH_NOTE, "Depth of SQL evidence in the analysed repositories, not a full measure of skill.");
});
