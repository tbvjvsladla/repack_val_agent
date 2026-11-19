import test from "node:test";
import assert from "node:assert/strict";
import { formatCents } from "../src/format.ts";

test("formats cents", () => {
  assert.equal(formatCents(1234), "12.34");
});
