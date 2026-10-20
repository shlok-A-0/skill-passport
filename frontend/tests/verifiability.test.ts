import assert from "node:assert/strict";
import { test } from "node:test";
import { latestPerStudent } from "../src/latestPerStudent.ts";

test("latest passport per student uses creation time, not a re-check update", () => {
  const passports = [
    { id: "latest", github_username: "student", created_at: "2026-10-20", updated_at: "2026-10-20" },
    { id: "old-rechecked", github_username: "STUDENT", created_at: "2026-10-01", updated_at: "2026-10-21" },
  ];

  assert.deepEqual(latestPerStudent(passports).map((passport) => passport.id), ["latest"]);
});
