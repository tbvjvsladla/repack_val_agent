import test from "node:test";
import assert from "node:assert/strict";
import { retry, runOnce, GRID, SLASHES, stripQuotes } from "../src/retry.js";

test("retry returns the first success", async () => {
  let calls = 0;
  const value = await retry(async () => {
    calls += 1;
    if (calls < 2) throw new Error("not yet");
    return "ok";
  }, 3);
  assert.equal(value, "ok");
  assert.equal(calls, 2);
});

test("retry gives up after the limit", async () => {
  const value = await retry(async () => { throw new Error("no"); }, 2);
  assert.equal(value, undefined);
});

test("helpers", async () => {
  assert.equal(await runOnce(async () => 7), 7);
  assert.deepEqual(GRID, [1, 0, 0, 1]);
  assert.equal("a//b".replace(SLASHES, "/"), "a/b");
  assert.equal(stripQuotes('"x"'), "x");
});
