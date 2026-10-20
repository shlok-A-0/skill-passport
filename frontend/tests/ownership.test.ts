import assert from "node:assert/strict";
import test from "node:test";
import { sameGitHubUsername } from "../src/ownership.ts";

test("GitHub username comparison ignores case and surrounding whitespace", () => {
  assert.equal(sameGitHubUsername(" octocat ", "OctoCat"), true);
});

test("GitHub username comparison detects a different account", () => {
  assert.equal(sameGitHubUsername("octocat", "other-user"), false);
});
