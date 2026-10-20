import assert from "node:assert/strict";
import test from "node:test";
import { validatePassportForm } from "../src/passportForm.ts";

function file(name: string, size = 100, type = "application/pdf"): File {
  return { name, size, type } as File;
}

test("passport form reports the missing resume specifically", () => {
  assert.equal(
    validatePassportForm({ username: "octocat", resume: null, role: "backend-intern", certificates: [] }),
    "Upload your resume PDF before generating a passport.",
  );
});

test("passport form rejects malformed usernames before submitting", () => {
  assert.match(
    validatePassportForm({ username: "bad_name", resume: file("resume.pdf"), role: "backend-intern", certificates: [] }) ?? "",
    /GitHub username/,
  );
});

test("passport form rejects invalid and oversized certificate uploads", () => {
  const base = { username: "octocat", resume: file("resume.pdf"), role: "backend-intern" };
  assert.match(validatePassportForm({ ...base, certificates: [file("notes.txt", 10, "text/plain")] }) ?? "", /certificates must be PDF/);
  assert.match(validatePassportForm({ ...base, certificates: [file("large.pdf", 11 * 1024 * 1024)] }) ?? "", /10 MB/);
});
