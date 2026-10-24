import assert from "node:assert/strict";
import test from "node:test";
import { DEMO_PROFILES, canAccessRoute } from "../src/authTypes.ts";

test("demo profiles configure recruiter and student credentials correctly", () => {
  assert.equal(DEMO_PROFILES.recruiter.user.role, "recruiter");
  assert.equal(DEMO_PROFILES.recruiter.user.email, "recruiter@techcorp.com");
  assert.equal(DEMO_PROFILES.recruiter.passwordHint, "recruiter2026");

  assert.equal(DEMO_PROFILES.student.user.role, "student");
  assert.equal(DEMO_PROFILES.student.user.email, "student@university.edu");
  assert.equal(DEMO_PROFILES.student.passwordHint, "student2026");
});

test("students can access create and student views, but not recruiter or placement", () => {
  assert.equal(canAccessRoute("/", "student"), true);
  assert.equal(canAccessRoute("/student", "student"), true);
  assert.equal(canAccessRoute("/passport/xyz-123", "student"), true);

  assert.equal(canAccessRoute("/recruiter", "student"), false);
  assert.equal(canAccessRoute("/recruiter/xyz-123", "student"), false);
  assert.equal(canAccessRoute("/recruiter/xyz-123/evidence", "student"), false);
  assert.equal(canAccessRoute("/placement", "student"), false);
});

test("recruiters have access to all application routes", () => {
  assert.equal(canAccessRoute("/", "recruiter"), true);
  assert.equal(canAccessRoute("/student", "recruiter"), true);
  assert.equal(canAccessRoute("/passport/xyz-123", "recruiter"), true);
  assert.equal(canAccessRoute("/recruiter", "recruiter"), true);
  assert.equal(canAccessRoute("/recruiter/xyz-123", "recruiter"), true);
  assert.equal(canAccessRoute("/recruiter/xyz-123/evidence", "recruiter"), true);
  assert.equal(canAccessRoute("/placement", "recruiter"), true);
});
