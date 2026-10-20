import assert from "node:assert/strict";
import { test } from "node:test";
import { consecutivePollFailuresAfterRequest, nextJobPollDelay } from "../src/jobPolling.ts";

test("job polling backs off and caps its retry delay", () => {
  assert.equal(nextJobPollDelay(1500), 3000);
  assert.equal(nextJobPollDelay(12_000), 15_000);
  assert.equal(nextJobPollDelay(15_000), 15_000);
});

test("completed-job passport load failures accumulate until polling pauses", () => {
  let failures = 0;
  for (let attempt = 0; attempt < 8; attempt += 1) {
    // The job status succeeds, but its follow-up passport request fails.
    failures = consecutivePollFailuresAfterRequest(failures, false);
  }
  assert.equal(failures, 8);
  assert.equal(consecutivePollFailuresAfterRequest(failures, true), 0);
});
