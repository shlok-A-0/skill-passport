import assert from "node:assert/strict";
import { test } from "node:test";
import {
  MAX_CONSECUTIVE_POLL_FAILURES,
  nextJobPollDelay,
  pollJobAttempt,
} from "../src/jobPolling.ts";

test("job polling backs off and caps its retry delay", () => {
  assert.equal(nextJobPollDelay(1500), 3000);
  assert.equal(nextJobPollDelay(12_000), 15_000);
  assert.equal(nextJobPollDelay(15_000), 15_000);
});

test("completed-job passport load failures accumulate until polling pauses", async () => {
  let failures = 0;
  let paused = false;
  let jobRequests = 0;
  let passportRequests = 0;

  while (!paused) {
    const attempt = await pollJobAttempt(
      failures,
      async () => {
        jobRequests += 1;
        return { status: "done" };
      },
      async () => {
        passportRequests += 1;
        throw new Error("passport request failed");
      },
    );
    assert.equal(attempt.ok, false);
    if (!attempt.ok) failures = attempt.consecutiveFailures;
    paused = failures >= MAX_CONSECUTIVE_POLL_FAILURES;
  }

  assert.equal(failures, MAX_CONSECUTIVE_POLL_FAILURES);
  assert.equal(paused, true);
  assert.equal(jobRequests, MAX_CONSECUTIVE_POLL_FAILURES);
  assert.equal(passportRequests, MAX_CONSECUTIVE_POLL_FAILURES);
});
