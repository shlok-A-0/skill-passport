import assert from "node:assert/strict";
import { test } from "node:test";
import { nextJobPollDelay } from "../src/jobPolling.ts";

test("job polling backs off and caps its retry delay", () => {
  assert.equal(nextJobPollDelay(1500), 3000);
  assert.equal(nextJobPollDelay(12_000), 15_000);
  assert.equal(nextJobPollDelay(15_000), 15_000);
});
