import assert from "node:assert/strict";
import { test } from "node:test";
import { showsNotCountedNote } from "../src/evidencePanel.ts";

const evidence = (type: "code" | "dependency" | "config" | "commit", authored?: boolean, test_file_group = false) => ({
  type,
  checks: authored === undefined ? {} : { authored },
  test_file_group,
} as never);

test("shows the not-counted note for unauthored dependency, config and grouped test evidence", () => {
  assert.equal(showsNotCountedNote(evidence("dependency", false)), true);
  assert.equal(showsNotCountedNote(evidence("config", false)), true);
  assert.equal(showsNotCountedNote(evidence("config", false, true)), true);
});

test("does not show the not-counted note for authored or unchecked evidence", () => {
  assert.equal(showsNotCountedNote(evidence("dependency", true)), false);
  assert.equal(showsNotCountedNote(evidence("config")), false);
  assert.equal(showsNotCountedNote(evidence("code", false)), false);
});
