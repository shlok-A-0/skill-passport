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

test("passport form rejects empty resume and certificate files", () => {
  const base = { username: "octocat", resume: file("resume.pdf"), role: "backend-intern" };
  assert.match(
    validatePassportForm({ ...base, resume: file("empty.pdf", 0), certificates: [] }) ?? "",
    /resume PDF is empty/,
  );
  assert.match(
    validatePassportForm({ ...base, certificates: [file("empty-certificate.pdf", 0)] }) ?? "",
    /certificate is empty/,
  );
});

test("passport form rejects malformed usernames before submitting", () => {
  assert.match(
    validatePassportForm({ username: "bad_name", resume: file("resume.pdf"), role: "backend-intern", certificates: [] }) ?? "",
    /GitHub username/,
  );
  for (const username of ["-octocat", "octocat-", "octo--cat"]) {
    assert.match(
      validatePassportForm({ username, resume: file("resume.pdf"), role: "backend-intern", certificates: [] }) ?? "",
      /GitHub username/,
    );
  }
});

test("passport form rejects invalid and oversized certificate uploads", () => {
  const base = { username: "octocat", resume: file("resume.pdf"), role: "backend-intern" };
  assert.match(
    validatePassportForm({ ...base, certificates: [file("notes.txt", 10, "text/plain")] }) ?? "",
    /Certificates must be PDFs, PNG images, or JPEG images/,
  );
  assert.match(validatePassportForm({ ...base, certificates: [file("large.pdf", 11 * 1024 * 1024)] }) ?? "", /10 MB/);
});

test("passport form checks optional project reports and the portfolio URL", () => {
  const base = { username: "octocat", resume: file("resume.pdf"), role: "backend-intern", certificates: [] };
  assert.equal(validatePassportForm({ ...base, reports: [file("report.pdf"), file("scan.png", 100, "image/png")], portfolioUrl: "https://octocat.github.io" }), null);
  assert.match(validatePassportForm({ ...base, reports: [file("a.pdf"), file("b.pdf"), file("c.pdf"), file("d.pdf")] }) ?? "", /3 project reports/);
  assert.match(validatePassportForm({ ...base, reports: [file("notes.txt", 10, "text/plain")] }) ?? "", /Project reports must be/);
  assert.match(validatePassportForm({ ...base, reports: [file("empty.pdf", 0)] }) ?? "", /report is empty/);
  assert.match(validatePassportForm({ ...base, certificates: [file("c.pdf", 9 * 1024 * 1024)], reports: [file("r1.pdf", 9 * 1024 * 1024), file("r2.pdf", 9 * 1024 * 1024)] }) ?? "", /reports must total 25 MB/);
  assert.match(validatePassportForm({ ...base, portfolioUrl: "http://octocat.github.io" }) ?? "", /https/);
  assert.match(validatePassportForm({ ...base, portfolioUrl: `https://x.io/${"a".repeat(300)}` }) ?? "", /300/);
  assert.equal(validatePassportForm({ ...base, portfolioUrl: "   " }), null);
});

test("passport form accepts PNG and JPEG certificates", () => {
  const base = { username: "octocat", resume: file("resume.pdf"), role: "backend-intern" };
  assert.equal(
    validatePassportForm({ ...base, certificates: [file("certificate.png", 100, "image/png")] }),
    null,
  );
  assert.equal(
    validatePassportForm({ ...base, certificates: [file("certificate.jpg", 100, "image/jpeg")] }),
    null,
  );
});
