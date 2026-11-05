import assert from "node:assert/strict";
import { test } from "node:test";
import { f } from "./source.mjs";

test("empty", () => assert.equal(f([]), ""));
test("priority", () => assert.equal(f(["low=0", "high=9"]), "high\nlow\n"));
test("stable ties", () => assert.equal(f(["b=2", "a=2", "c=3"]), "c\nb\na\n"));
test("duplicates", () => assert.equal(f(["a=1", "a=1"]), "a\na\n"));
test("malformed", () => {
  for (const input of ["a", "a=10", "a=-1", "é=2", "a=2\n"]) {
    assert.throws(() => f([input]), /invalid task/);
  }
});
