// Checks of task_executor.mjs with EXPLICIT FAKE native child processes (test fixtures only: real spawn, real exit codes, raw files). They are not production
// mocks, not evidence of any real model profile, availability or campaign readiness, and no real CLI, network, LLM or target project is ever started.
import assert from 'node:assert/strict';
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';
import { buildReceipt, judgeModel, observeClaude, observeCodex, receiptJob, resumeIsStartPlusSession, resumeJob, startJob, statusJob, validateRequest, InputError, CAPACITY } from '../task_executor.mjs';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const EXECUTOR = path.join(HERE, '..', 'task_executor.mjs');
assert.ok(process.env.TMPDIR, 'TMPDIR must name the private temporary directory');
const BASE = fs.mkdtempSync(path.join(process.env.TMPDIR, 'task-executor-'));
const SID = '11111111-2222-4333-8444-555555555555';
const OTHER_SID = '99999999-2222-4333-8444-555555555555';
let counter = 0;

// ------------------------------------------------------------------------------------------------------------ fake native CLI
const FAKE = `#!${process.execPath}
const fs = require('node:fs'); const path = require('node:path');
const plan = JSON.parse(fs.readFileSync(process.env.FAKE_PLAN, 'utf8'));
const countFile = path.join(plan.dir, 'count');
const n = (fs.existsSync(countFile) ? Number(fs.readFileSync(countFile, 'utf8')) : 0) + 1;
fs.writeFileSync(countFile, String(n));
let stdin = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (d) => { stdin += d; });
process.stdin.on('end', () => {
  const step = plan.steps[Math.min(n, plan.steps.length) - 1];
  fs.writeFileSync(path.join(plan.dir, 'invocation-' + n + '.json'), JSON.stringify({ argv: process.argv.slice(2), cwd: process.cwd(), stdin, env_has_secret: Boolean(process.env.FAKE_SECRET) }));
  if (step.final) { const at = process.argv.indexOf('-o'); if (at > 0) fs.writeFileSync(process.argv[at + 1], step.final); }
  if (step.stdout) process.stdout.write(step.stdout);
  if (step.stderr) process.stderr.write(step.stderr);
  if (step.kill) process.kill(process.pid, step.kill);
  setTimeout(() => process.exit(step.exit ?? 0), step.sleep_ms ?? 0);
});
`;

const codex = (sid, ...events) => [{ type: 'thread.started', thread_id: sid }, ...events].map((e) => JSON.stringify(e)).join('\n') + '\n';
const done = { type: 'turn.completed' };
const capacityEvents = (sid, message = CAPACITY) => codex(sid, { type: 'error', message }, { type: 'turn.failed', error: { message } });
const claudeWrapper = (extra = {}) => JSON.stringify({ type: 'result', subtype: 'success', is_error: false, result: 'done', session_id: SID, num_turns: 3, ...extra }) + '\n';

function area(name) {
  const dir = path.join(BASE, `${name}-${++counter}`);
  fs.mkdirSync(dir);
  const exe = path.join(dir, 'fake-native');
  fs.writeFileSync(exe, FAKE, { mode: 0o755 });
  const workspace = path.join(dir, 'workspace');
  fs.mkdirSync(workspace);
  const planDir = path.join(dir, 'plan');
  fs.mkdirSync(planDir);
  fs.writeFileSync(path.join(dir, 'prompt.txt'), 'PUBLIC TASK PROMPT\n');
  return { dir, exe, workspace, planDir, job: path.join(dir, 'job'), requestFile: path.join(dir, 'request.json'), clock: 0, delays: [] };
}

function plan(a, steps) {
  fs.rmSync(path.join(a.planDir, 'count'), { force: true });
  fs.writeFileSync(path.join(a.dir, 'plan.json'), JSON.stringify({ dir: a.planDir, steps }));
  process.env.FAKE_PLAN = path.join(a.dir, 'plan.json');
}

function requestOf(a, override = {}) {
  const base = {
    schema: 'task-executor-request/2',
    request_identity: { attempt_id: 'c1-normal-1', case: { id: 'normal', n: [1, 2], text: '한글 ✓' }, nested: { a: null } },
    comparison_key: 'input-v1-env-v1-profile-v1',
    executor_id: 'real-task-executor',
    evidence_mode: 'fixture',
    workspace: a.workspace,
    prompt_file: path.join(a.dir, 'prompt.txt'),
    provider: { kind: 'codex', executable: a.exe },
    profile: { main_profile_id: 'profile-1', sub_profile_id: 'profile-1', cli: 'codex-fixture', model: 'model-fixture', effort: 'low', expected_primary_models: null },
    budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 2, backoff_seconds: 0, burst_seconds: 100 },
  };
  return { ...base, ...override };
}

function write(a, override) {
  const value = requestOf(a, override);
  fs.writeFileSync(a.requestFile, JSON.stringify(value, null, 2));
  return value;
}

const deps = (a) => ({
  sleep: async (ms) => { a.delays.push(ms); a.clock += ms; },
  now: () => a.clock,
});

const invocation = (a, n) => JSON.parse(fs.readFileSync(path.join(a.planDir, `invocation-${n}.json`), 'utf8'));
const invocations = (a) => (fs.existsSync(path.join(a.planDir, 'count')) ? Number(fs.readFileSync(path.join(a.planDir, 'count'), 'utf8')) : 0);
const jobJson = (a) => JSON.parse(fs.readFileSync(path.join(a.job, 'job.json'), 'utf8'));

function assertReceiptShape(receipt) {
  assert.deepEqual(Object.keys(receipt), ['identity', 'status', 'executor_id', 'comparison_key', 'evidence', 'metrics', 'note']);
  assert.deepEqual(Object.keys(receipt.metrics), ['compute_ms', 'wall_ms', 'cost_usd', 'input_tokens', 'output_tokens']);
  assert.ok(['PASS', 'FAIL', 'NOT_RUN'].includes(receipt.status));
  for (const file of receipt.evidence) assert.ok(fs.existsSync(file), `evidence must exist: ${file}`);
  assert.equal(receipt.checks, undefined);
  assert.equal(receipt.evaluator_id, undefined);
}

const readReceipt = (a) => JSON.parse(fs.readFileSync(path.join(a.job, 'receipt.json'), 'utf8'));

// ------------------------------------------------------------------------------------------------------------ success, identity, raw
test('a real child returns: identity echoed unchanged, raw/argv/cwd/pid/session/exit persisted, execution PASS only, metrics null except measured wall', async () => {
  const a = area('success');
  process.env.FAKE_SECRET = 'must-not-be-copied';
  plan(a, [{ stdout: codex(SID, { type: 'item.started', item: { id: 'i1' } }, { type: 'item.completed', item: { id: 'i1' } }, done) }]);
  const request = write(a);
  const result = await startJob(a.job, a.requestFile, deps(a));
  assert.equal(result.code, 0);
  assert.equal(result.state, 'returned');
  const receipt = readReceipt(a);
  assertReceiptShape(receipt);
  assert.deepEqual(receipt.identity, request.request_identity);
  assert.equal(receipt.status, 'PASS');
  assert.equal(receipt.executor_id, 'real-task-executor');
  assert.equal(receipt.comparison_key, request.comparison_key);
  assert.equal(receipt.metrics.compute_ms, null);
  assert.equal(receipt.metrics.cost_usd, null);
  assert.equal(receipt.metrics.input_tokens, null);
  assert.equal(receipt.metrics.output_tokens, null);
  assert.equal(typeof receipt.metrics.wall_ms, 'number');
  assert.match(receipt.note, /execution report only, not a business verdict/);
  assert.match(receipt.note, /observed_primary_model=UNREPORTED; model_check=NO_EXPECTATION; observed_effort=UNREPORTED/);
  assert.match(receipt.note, /evidence_mode=fixture/);
  // the real invocation: argv array, cwd, stdin prompt, inherited env (no copied secret in any persisted file)
  const seen = invocation(a, 1);
  assert.deepEqual(seen.argv, ['exec', '--json', '-m', 'model-fixture', '-c', 'model_reasoning_effort="low"', '-o', path.join(a.job, 'attempts', 'attempt-0001', 'final.txt'), '-']);
  assert.equal(seen.cwd, fs.realpathSync(a.workspace));
  assert.equal(seen.stdin, 'PUBLIC TASK PROMPT\n');
  assert.equal(seen.env_has_secret, true);
  const dir = path.join(a.job, 'attempts', 'attempt-0001');
  const exit = JSON.parse(fs.readFileSync(path.join(dir, 'exit.json'), 'utf8'));
  assert.equal(exit.exit_code, 0);
  const proc = JSON.parse(fs.readFileSync(path.join(dir, 'process.json'), 'utf8'));
  assert.ok(proc.pid > 0);
  const job = jobJson(a);
  assert.equal(job.native_session_id, SID);
  assert.deepEqual(job.observed, { models: [], usage_models: [], child_models: [], model_check: 'NO_EXPECTATION', effort: null });
  assert.equal(job.requested.model, 'model-fixture');
  for (const file of fs.readdirSync(a.job, { recursive: true })) {
    const full = path.join(a.job, file);
    if (fs.statSync(full).isFile()) assert.ok(!fs.readFileSync(full, 'utf8').includes('must-not-be-copied'), file);
  }
  delete process.env.FAKE_SECRET;
});

// ------------------------------------------------------------------------------------------------------------ structured outcomes
test('exit 0 is not success by itself: structured errors, denials, empty results and max-turns are classified from the native wrapper', async () => {
  const cases = [
    ['is_error', claudeWrapper({ is_error: true, subtype: 'error_during_execution', result: 'x' }), 'native_error', 'FAIL'],
    ['denials-on-success', claudeWrapper({ permission_denials: [{ tool_name: 'Edit' }] }), 'returned_with_denials', 'PASS'],
    ['denials-on-error', claudeWrapper({ is_error: true, subtype: 'error_during_execution', permission_denials: [{ tool_name: 'Edit' }] }), 'permission_denied', 'FAIL'],
    ['empty', claudeWrapper({ result: '' }), 'native_failure', 'FAIL'],
    ['not json', 'plain text, not a wrapper\n', 'invalid_events', 'FAIL'],
    ['success', claudeWrapper(), 'returned', 'PASS'],
  ];
  for (const [name, stdout, state, status] of cases) {
    const a = area('claude-' + name.replace(' ', '-'));
    plan(a, [{ stdout, exit: 0 }]);
    write(a, { provider: { kind: 'claude', executable: a.exe, start_argv: ['-p', '--output-format', 'json', '--model', '{model}', '--effort', '{effort}', '--max-turns', '{max_turns}'], resume_argv: ['-p', '--output-format', 'json', '--model', '{model}', '--effort', '{effort}', '--max-turns', '{max_turns}', '--resume', '{session_id}'] }, budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 0, backoff_seconds: 0, burst_seconds: 100, max_turns: 5 } });
    const result = await startJob(a.job, a.requestFile, deps(a));
    assert.equal(result.state, state, name);
    assert.equal(readReceipt(a).status, status, name);
    assertReceiptShape(readReceipt(a));
    assert.equal(invocations(a), 1, `${name}: not retried`);
  }
});

test('codex: exit 0 with turn.failed, exit 1 without capacity, and incomplete turns are failures that are not retried', async () => {
  for (const [name, events, exit, state] of [
    ['failed-exit0', codex(SID, { type: 'turn.failed', error: { message: 'boom' } }), 0, 'native_failure'],
    ['429-text', codex(SID, { type: 'error', message: '429 rate limit' }, { type: 'turn.failed', error: { message: '429 rate limit' } }), 1, 'native_failure'],
    ['mixed-errors', codex(SID, { type: 'error', message: CAPACITY }, { type: 'error', message: 'other' }, { type: 'turn.failed', error: { message: CAPACITY } }), 1, 'native_failure'],
    ['no-turn-end', codex(SID), 0, 'native_failure'],
  ]) {
    const a = area(name);
    plan(a, [{ stdout: events, exit }]);
    write(a);
    const result = await startJob(a.job, a.requestFile, deps(a));
    assert.equal(result.state, state, name);
    assert.equal(invocations(a), 1, `${name}: only exact structured capacity is retried`);
    assert.equal(readReceipt(a).status, 'FAIL');
  }
  const a = area('stderr-capacity');
  plan(a, [{ stdout: codex(SID, { type: 'turn.failed', error: { message: 'x' } }), stderr: CAPACITY, exit: 1 }]);
  write(a);
  assert.equal((await startJob(a.job, a.requestFile, deps(a))).state, 'native_failure', 'stderr text is never read as capacity');
});

test('malformed events, a session mismatch, in-flight items, timeout and signal are separate non-retried states', async () => {
  const cases = [
    ['malformed', { stdout: '{not json}\n', exit: 1 }, 'invalid_events'],
    ['no-session', { stdout: JSON.stringify(done) + '\n', exit: 0 }, 'session_unresolved'],
    ['two-sessions', { stdout: codex(SID) + codex(OTHER_SID, done), exit: 0 }, 'session_unresolved'],
    ['inflight-items', { stdout: codex(SID, { type: 'item.started', item: { id: 'cmd' } }, done), exit: 0 }, 'inflight_items'],
    ['signal', { stdout: codex(SID), kill: 'SIGKILL' }, 'process_signal'],
  ];
  for (const [name, step, state] of cases) {
    const a = area(name);
    plan(a, [step]);
    write(a);
    const result = await startJob(a.job, a.requestFile, deps(a));
    assert.equal(result.state, state, name);
    assert.equal(invocations(a), 1, name);
    assert.equal(readReceipt(a).status, 'FAIL', name);
  }
  const t = area('timeout');
  plan(t, [{ stdout: codex(SID), sleep_ms: 30000 }]);
  write(t, { budget: { attempt_timeout_seconds: 1, additional_capacity_attempts: 2, backoff_seconds: 0, burst_seconds: 100 } });
  const started = Date.now();
  const result = await startJob(t.job, t.requestFile, deps(t));
  assert.equal(result.state, 'timeout');
  assert.ok(Date.now() - started < 15000, 'the child group is stopped at the attempt timeout');
  assert.equal(invocations(t), 1);
  assert.equal(JSON.parse(fs.readFileSync(path.join(t.job, 'attempts', 'attempt-0001', 'exit.json'), 'utf8')).timed_out, true);
});

// ------------------------------------------------------------------------------------------------------------ model identity
test('codex model evidence: only a top-level model of thread/turn events is primary; payload models are ignored; the request is never copied into observed', async () => {
  const expectedSet = (m) => ({ profile: { ...requestOf(area('x')).profile, expected_primary_models: m } });
  const a = area('model-unknown');
  plan(a, [{ stdout: codex(SID, { type: 'item.completed', item: { id: 'i', model: 'payload-model' } }, done) }]);
  write(a, expectedSet(['model-fixture']));
  assert.equal((await startJob(a.job, a.requestFile, deps(a))).state, 'returned');
  assert.deepEqual(jobJson(a).observed.models, []);
  assert.equal(jobJson(a).observed.model_check, 'UNVERIFIED');
  const m = area('model-match');
  plan(m, [{ stdout: codex(SID, { type: 'turn.started', model: 'model-fixture' }, done) }]);
  write(m, expectedSet(['model-fixture']));
  await startJob(m.job, m.requestFile, deps(m));
  assert.deepEqual(jobJson(m).observed.models, ['model-fixture']);
  assert.equal(jobJson(m).observed.model_check, 'MATCH');
  assert.match(readReceipt(m).note, /observed_primary_model=model-fixture; model_check=MATCH/);
  const x = area('model-mismatch');
  plan(x, [{ stdout: codex(SID, { type: 'turn.started', model: 'another-model' }, done) }]);
  write(x, expectedSet(['model-fixture']));
  assert.equal((await startJob(x.job, x.requestFile, deps(x))).state, 'identity_mismatch');
  assert.equal(readReceipt(x).status, 'FAIL');
  assert.deepEqual(jobJson(x).observed.models, ['another-model']);
  // no expectation: the reported primary is recorded, nothing is judged
  const n = area('model-noexpect');
  plan(n, [{ stdout: codex(SID, { type: 'turn.started', model: 'whatever' }, done) }]);
  write(n);
  assert.equal((await startJob(n.job, n.requestFile, deps(n))).state, 'returned');
  assert.equal(jobJson(n).observed.model_check, 'NO_EXPECTATION');
  // an explicit mismatch outranks capacity: no retry, no model switch
  const c = area('model-capacity');
  plan(c, [{ stdout: codex(SID, { type: 'turn.started', model: 'other' }, { type: 'error', message: CAPACITY }, { type: 'turn.failed', error: { message: CAPACITY } }), exit: 1 }]);
  write(c, expectedSet(['model-fixture']));
  assert.equal((await startJob(c.job, c.requestFile, deps(c))).state, 'identity_mismatch');
  assert.equal(invocations(c), 1);
  assert.equal(judgeModel([{ value: 'a' }], ['a', 'b']), 'MATCH');
  assert.equal(judgeModel([], ['a']), 'UNVERIFIED');
  assert.equal(judgeModel([{ value: 'a' }], null), 'NO_EXPECTATION');
});

test('wrapper usage/cost/duration is raw evidence only: receipt metrics stay null except the measured wall time', async () => {
  const a = area('usage');
  plan(a, [{ stdout: claudeWrapper({ total_cost_usd: 1.25, duration_api_ms: 9999, usage: { input_tokens: 10, output_tokens: 20 }, modelUsage: { 'model-fixture': { costUSD: 1.25 } } }) }]);
  write(a, { provider: { kind: 'claude', executable: a.exe, start_argv: ['-p', '--model', '{model}', '--effort', '{effort}'], resume_argv: ['-p', '--model', '{model}', '--effort', '{effort}', '--resume', '{session_id}'] } });
  await startJob(a.job, a.requestFile, deps(a));
  const receipt = readReceipt(a);
  assert.deepEqual({ ...receipt.metrics, wall_ms: null }, { compute_ms: null, wall_ms: null, cost_usd: null, input_tokens: null, output_tokens: null });
  assert.ok(receipt.metrics.wall_ms >= 0);
  const native = JSON.parse(fs.readFileSync(path.join(a.job, 'attempts', 'attempt-0001', 'native.json'), 'utf8'));
  assert.equal(native.usage_raw.total_cost_usd, 1.25);
  assert.match(native.usage_raw.scope, /unknown/);
});

// ------------------------------------------------------------------------------------------------------------ lock / duplicate
test('a duplicate start, a held lock and a stale lock are refused without spawning or deleting anything', async () => {
  const a = area('lock');
  plan(a, [{ stdout: capacityEvents(SID), exit: 1 }]);
  write(a, { budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 0, backoff_seconds: 0, burst_seconds: 100 } });
  assert.equal((await startJob(a.job, a.requestFile, deps(a))).state, 'capacity_deferred');
  const before = invocations(a);
  const duplicate = await startJob(a.job, a.requestFile, deps(a));
  assert.equal(duplicate.code, 3);
  assert.equal(duplicate.state, 'duplicate_job');
  // a live holder (this very process)
  const lockFile = path.join(a.job, '.lock');
  const ticks = fs.readFileSync(`/proc/${process.pid}/stat`, 'utf8');
  fs.writeFileSync(lockFile, JSON.stringify({ pid: process.pid, start_ticks: ticks.slice(ticks.lastIndexOf(')') + 2).split(' ')[19], token: 'x' }));
  const live = await resumeJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([live.code, live.state], [3, 'locked_inflight']);
  assert.equal(statusJob(a.job).state, 'locked_inflight');
  // a stale holder: reported, never deleted
  fs.writeFileSync(lockFile, JSON.stringify({ pid: 2 ** 22 + 12345, start_ticks: '1', token: 'y' }));
  const stale = await resumeJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([stale.code, stale.state], [3, 'stale_lock']);
  assert.ok(fs.existsSync(lockFile));
  assert.equal(invocations(a), before);
  fs.unlinkSync(lockFile);
});

test('two concurrent starts of the same job directory: exactly one runs', async () => {
  const a = area('concurrent');
  plan(a, [{ stdout: codex(SID, done), sleep_ms: 300 }]);
  write(a);
  const results = await Promise.all([startJob(a.job, a.requestFile, deps(a)), startJob(a.job, a.requestFile, deps(a))]);
  assert.deepEqual(results.map((r) => r.state).sort(), ['duplicate_job', 'returned']);
  assert.equal(invocations(a), 1);
});

// ------------------------------------------------------------------------------------------------------------ capacity / resume
test('exact capacity warning -> bounded backoff -> the SAME native session, model, effort, workspace and permissions -> returned', async () => {
  const a = area('capacity');
  plan(a, [
    { stdout: capacityEvents(SID), exit: 1 },
    { stdout: capacityEvents(SID, CAPACITY.replace(/\.$/, '')), exit: 1 },
    { stdout: codex(SID, done), exit: 0 },
  ]);
  write(a, { budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 2, backoff_seconds: 4, burst_seconds: 200 } });
  const result = await startJob(a.job, a.requestFile, deps(a));
  assert.equal(result.state, 'returned');
  assert.deepEqual(a.delays, [4000, 8000]);
  const first = invocation(a, 1);
  const second = invocation(a, 2);
  const third = invocation(a, 3);
  assert.equal(first.argv.includes('resume'), false);
  for (const resumed of [second, third]) {
    assert.deepEqual(resumed.argv.slice(0, 3), ['exec', 'resume', '--json']);
    assert.ok(resumed.argv.includes(SID));
    assert.ok(!resumed.argv.includes('--last'));
    assert.equal(resumed.argv[resumed.argv.indexOf('-m') + 1], 'model-fixture');
    assert.equal(resumed.argv[resumed.argv.indexOf('-c') + 1], 'model_reasoning_effort="low"');
    assert.equal(resumed.cwd, fs.realpathSync(a.workspace));
    assert.match(resumed.stdin, /^Resume this same task\. Inspect saved progress and any running command before acting/);
    assert.ok(resumed.stdin.endsWith('PUBLIC TASK PROMPT\n'));
  }
  assert.equal(jobJson(a).attempts.length, 3);
  assert.equal(readReceipt(a).status, 'PASS');
  assert.equal(readReceipt(a).metrics.wall_ms >= 0, true);
});

test('an exhausted burst is a checkpoint (open, no receipt, exit 75), the next burst resumes the same session with backoff first, and a terminal receipt is never overwritten', async () => {
  const a = area('exhausted');
  plan(a, [1, 2, 3, 4].map(() => ({ stdout: capacityEvents(SID), exit: 1 })).concat([{ stdout: codex(SID, done) }]));
  const request = write(a, { budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 1, backoff_seconds: 3, burst_seconds: 100 } });
  const first = await startJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([first.code, first.state, first.open, first.receipt], [75, 'capacity_deferred', true, null]);
  assert.deepEqual(a.delays, [3000]);
  assert.equal(fs.existsSync(path.join(a.job, 'receipt.json')), false);
  const status = statusJob(a.job);
  assert.deepEqual([status.state, status.open, status.resumable, status.native_session_id], ['capacity_deferred', true, true, SID]);
  assert.equal(receiptJob(a.job).code, 3, 'no receipt while the job is open');
  a.delays.length = 0;
  const second = await resumeJob(a.job, a.requestFile, deps(a));
  assert.equal(second.state, 'capacity_deferred');
  assert.deepEqual(a.delays, [3000, 6000], 'the first resume of a later burst still waits (offset by one)');
  const third = await resumeJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([third.code, third.state], [0, 'returned']);
  assert.equal(readReceipt(a).status, 'PASS');
  assert.deepEqual(readReceipt(a).identity, request.request_identity);
  const frozen = fs.readFileSync(path.join(a.job, 'receipt.json'), 'utf8');
  const again = await resumeJob(a.job, a.requestFile, deps(a));
  assert.equal(again.code, 3);
  assert.equal(receiptJob(a.job).written, false);
  assert.equal(fs.readFileSync(path.join(a.job, 'receipt.json'), 'utf8'), frozen);
  assert.equal(invocations(a), 5);
  assert.equal(jobJson(a).bursts.length, 3);
  assert.ok(jobJson(a).bursts.every((b) => typeof b.wall_ms === 'number' && b.ended_utc));
});

test('the burst bound: not enough budget for the next backoff + attempt ends the burst early; an impossible budget is refused before any spawn', async () => {
  const a = area('bound');
  plan(a, [{ stdout: capacityEvents(SID), exit: 1 }]);
  write(a, { budget: { attempt_timeout_seconds: 5, additional_capacity_attempts: 3, backoff_seconds: 2, burst_seconds: 8 } });
  const result = await startJob(a.job, a.requestFile, deps(a));
  assert.equal(result.state, 'capacity_deferred');
  assert.equal(invocations(a), 2, 'a third attempt does not fit the burst');
  const b = area('impossible');
  plan(b, [{ stdout: codex(SID, done) }]);
  write(b, { budget: { attempt_timeout_seconds: 5, additional_capacity_attempts: 1, backoff_seconds: 2, burst_seconds: 7 } });
  await assert.rejects(startJob(b.job, b.requestFile, deps(b)), InputError);
  assert.equal(invocations(b), 0);
  assert.equal(fs.existsSync(b.job), false);
});

test('budget exhaustion (max turns) is an open checkpoint resumed in the same Claude session, not completion', async () => {
  const a = area('max-turns');
  plan(a, [{ stdout: claudeWrapper({ subtype: 'error_max_turns', is_error: true, result: '' }) }, { stdout: claudeWrapper() }]);
  const provider = { kind: 'claude', executable: a.exe, start_argv: ['-p', '--model', '{model}', '--effort', '{effort}', '--max-turns', '{max_turns}'], resume_argv: ['-p', '--model', '{model}', '--effort', '{effort}', '--max-turns', '{max_turns}', '--resume', '{session_id}'] };
  write(a, { provider, budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 0, backoff_seconds: 0, burst_seconds: 100, max_turns: 7 } });
  const first = await startJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([first.code, first.state, first.open, first.receipt], [75, 'budget_exhausted', true, null]);
  assert.deepEqual(invocation(a, 1).argv, ['-p', '--model', 'model-fixture', '--effort', 'low', '--max-turns', '7']);
  const second = await resumeJob(a.job, a.requestFile, deps(a));
  assert.equal(second.state, 'returned');
  assert.deepEqual(invocation(a, 2).argv, ['-p', '--model', 'model-fixture', '--effort', 'low', '--max-turns', '7', '--resume', SID]);
});

test('an explicit caller decision can close an open job as NOT_RUN/FAIL; it is a caller receipt, never a success', async () => {
  const a = area('close-open');
  plan(a, [{ stdout: capacityEvents(SID), exit: 1 }]);
  write(a, { budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 0, backoff_seconds: 0, burst_seconds: 100 } });
  await startJob(a.job, a.requestFile, deps(a));
  assert.throws(() => receiptJob(a.job, { status: 'PASS', note: 'no' }), InputError);
  assert.throws(() => receiptJob(a.job, { status: 'NOT_RUN', note: '' }), InputError);
  const closed = receiptJob(a.job, { status: 'NOT_RUN', note: 'caller stopped the checkpoint' });
  assert.equal(closed.status, 'NOT_RUN');
  assertReceiptShape(readReceipt(a));
  assert.match(readReceipt(a).note, /closed by explicit caller decision: caller stopped the checkpoint/);
  assert.equal((await resumeJob(a.job, a.requestFile, deps(a))).code, 3, 'a closed job is not resumed');
});

// ------------------------------------------------------------------------------------------------------------ changed inputs
test('resume rejects a changed prompt, profile, model, effort, workspace, executable, budget or permission template', async () => {
  const a = area('changed');
  plan(a, [{ stdout: capacityEvents(SID), exit: 1 }, { stdout: codex(SID, done) }]);
  const original = write(a, { budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 0, backoff_seconds: 0, burst_seconds: 100 } });
  await startJob(a.job, a.requestFile, deps(a));
  const before = invocations(a);
  const variants = {
    model: { profile: { ...original.profile, model: 'other-model' } },
    effort: { profile: { ...original.profile, effort: 'high' } },
    profile_ids: { profile: { ...original.profile, main_profile_id: 'p2', sub_profile_id: 'p2' } },
    executable: { provider: { kind: 'codex', executable: a.exe + '-other' } },
    budget: { budget: { ...original.budget, burst_seconds: 101 } },
    expected_models: { profile: { ...original.profile, expected_primary_models: ['x'] } },
    identity: { request_identity: { attempt_id: 'different' } },
    comparison_key: { comparison_key: 'other' },
    permission_template: { provider: { kind: 'codex', executable: a.exe, start_argv: ['exec', '--json', '-s', 'read-only', '-m', '{model}', '-c', 'model_reasoning_effort={effort_json}', '-o', '{final_file}', '-'], resume_argv: ['exec', 'resume', '--json', '-s', 'read-only', '-m', '{model}', '-c', 'model_reasoning_effort={effort_json}', '-o', '{final_file}', '{session_id}', '-'] } },
  };
  for (const [name, override] of Object.entries(variants)) {
    write(a, { ...original, ...override });
    const result = await resumeJob(a.job, a.requestFile, deps(a));
    assert.equal(result.code, 2, name);
    assert.equal(result.state, 'changed_input', name);
  }
  fs.mkdirSync(path.join(a.dir, 'workspace2'));
  write(a, { workspace: path.join(a.dir, 'workspace2') });
  assert.equal((await resumeJob(a.job, a.requestFile, deps(a))).code, 2);
  write(a, original);
  fs.writeFileSync(path.join(a.dir, 'prompt.txt'), 'CHANGED PROMPT\n');
  const prompt = await resumeJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([prompt.code, prompt.changed.includes('prompt')], [2, true]);
  assert.equal(invocations(a), before, 'nothing was spawned by any rejected resume');
  fs.writeFileSync(path.join(a.dir, 'prompt.txt'), 'PUBLIC TASK PROMPT\n');
  assert.equal((await resumeJob(a.job, a.requestFile, deps(a))).state, 'returned');
});

test('request validation: strict keys, explicit profile, same main/sub id, no dangerous flags, resume template drift, --last, missing model placeholder', () => {
  const a = area('validation');
  const good = requestOf(a);
  assert.doesNotThrow(() => validateRequest(good));
  const bad = (override, pattern) => assert.throws(() => validateRequest(requestOf(a, override)), (e) => e instanceof InputError && pattern.test(e.message));
  bad({ extra: 1 }, /request keys must be exactly/);
  bad({ evidence_mode: 'real' }, /evidence_mode/);
  bad({ request_identity: null }, /request_identity/);
  bad({ profile: { ...good.profile, sub_profile_id: 'different' } }, /same profile id/);
  bad({ profile: { ...good.profile, model: '' } }, /profile\.model/);
  bad({ profile: { ...good.profile, effort: null } }, /profile\.effort/);
  bad({ workspace: 'relative/path' }, /absolute/);
  bad({ budget: { ...good.budget, additional_capacity_attempts: 11 } }, /0\.\.10/);
  bad({ provider: { kind: 'codex', executable: a.exe, resume_argv: ['exec', 'resume', '--last', '--json', '-m', '{model}', '-c', '{effort_json}', '{session_id}', '-'], start_argv: ['exec', '--json', '-m', '{model}', '-c', '{effort_json}', '-'] } }, /forbidden argv token/);
  for (const flag of ['--dangerously-skip-permissions', '--dangerously-bypass-approvals-and-sandbox', '--ignore-user-config', '--ignore-rules']) {
    bad({ provider: { kind: 'codex', executable: a.exe, start_argv: ['exec', flag, '-m', '{model}', '-c', '{effort_json}', '-'], resume_argv: ['exec', 'resume', flag, '-m', '{model}', '-c', '{effort_json}', '{session_id}', '-'] } }, /forbidden argv token/);
  }
  bad({ provider: { kind: 'codex', executable: a.exe, start_argv: ['exec', '-m', '{model}', '-c', '{effort_json}', '-'], resume_argv: ['exec', 'resume', '-m', '{model}', '-c', '{effort_json}', '-'] } }, /\{session_id\}/);
  bad({ provider: { kind: 'codex', executable: a.exe, start_argv: ['exec', '-s', 'read-only', '-m', '{model}', '-c', '{effort_json}', '-'], resume_argv: ['exec', 'resume', '-m', '{model}', '-c', '{effort_json}', '{session_id}', '-'] } }, /must equal the start template/);
  bad({ provider: { kind: 'codex', executable: a.exe, start_argv: ['exec', '-m', '{model}', '-c', '{effort_json}', '-'], resume_argv: ['exec', 'resume', '--full-auto', '-m', '{model}', '-c', '{effort_json}', '{session_id}', '-'] } }, /must equal the start template/);
  bad({ provider: { kind: 'codex', executable: a.exe, start_argv: ['exec', '-c', '{effort_json}', '-'], resume_argv: ['exec', 'resume', '-c', '{effort_json}', '{session_id}', '-'] } }, /declared model/);
  bad({ provider: { kind: 'codex', executable: a.exe, start_argv: ['exec', '-m', '{model}', '-'], resume_argv: ['exec', 'resume', '-m', '{model}', '{session_id}', '-'] } }, /declared effort/);
  bad({ provider: { kind: 'codex', executable: a.exe, start_argv: ['exec', '-m', '{model}', '-c', '{effort_json}', '-', '{nope}'], resume_argv: ['exec', 'resume', '-m', '{model}', '-c', '{effort_json}', '{session_id}', '-', '{nope}'] } }, /unknown placeholder/);
  bad({ provider: { kind: 'claude', executable: a.exe } }, /required/);
});

// ------------------------------------------------------------------------------------------------------------ stale / running / unknown
function seedCheckpoint(a, mutate) {
  const dir = path.join(a.job, 'attempts', 'attempt-0001');
  const job = jobJson(a);
  mutate(job, dir);
  fs.writeFileSync(path.join(a.job, 'job.json'), JSON.stringify(job, null, 2));
}

test('stale/running/unknown windows never replay: unknown launch window, a live child, a vanished child — status reports, resume spawns nothing', async () => {
  const a = area('windows');
  plan(a, [{ stdout: capacityEvents(SID), exit: 1 }, { stdout: codex(SID, done) }]);
  write(a, { budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 0, backoff_seconds: 0, burst_seconds: 100 } });
  await startJob(a.job, a.requestFile, deps(a));
  const before = invocations(a);
  const dir = path.join(a.job, 'attempts', 'attempt-0001');
  const keep = { exit: fs.readFileSync(path.join(dir, 'exit.json'), 'utf8'), proc: fs.readFileSync(path.join(dir, 'process.json'), 'utf8'), job: fs.readFileSync(path.join(a.job, 'job.json'), 'utf8') };
  // (1) crashed between persisting the launch and spawning: no pid, no exit
  fs.unlinkSync(path.join(dir, 'exit.json'));
  fs.unlinkSync(path.join(dir, 'process.json'));
  seedCheckpoint(a, (job) => { Object.assign(job.attempts[0], { state: 'starting', pid: null, exit_code: null }); job.state = 'working'; });
  assert.equal(statusJob(a.job).state, 'unknown_launch_window');
  let result = await resumeJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([result.code, result.state], [4, 'unknown_launch_window']);
  // (2) a live child (a real sleeping process with its recorded start ticks)
  const sleeper = spawn(process.execPath, ['-e', 'setTimeout(()=>{}, 60000)'], { stdio: 'ignore', detached: true });
  const stat = fs.readFileSync(`/proc/${sleeper.pid}/stat`, 'utf8');
  fs.writeFileSync(path.join(dir, 'process.json'), JSON.stringify({ pid: sleeper.pid, start_ticks: stat.slice(stat.lastIndexOf(')') + 2).split(' ')[19] }));
  seedCheckpoint(a, (job) => { Object.assign(job.attempts[0], { state: 'working', pid: sleeper.pid }); });
  assert.equal(statusJob(a.job).state, 'inflight');
  result = await resumeJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([result.code, result.state], [4, 'inflight']);
  // the same pid with a different start time is a reused pid, not our child
  fs.writeFileSync(path.join(dir, 'process.json'), JSON.stringify({ pid: sleeper.pid, start_ticks: '1' }));
  assert.equal(statusJob(a.job).state, 'unknown_exit');
  process.kill(sleeper.pid, 'SIGKILL');
  await new Promise((resolve) => sleeper.once('exit', resolve));
  // (3) the child is gone and no exit was recorded
  assert.equal(statusJob(a.job).state, 'unknown_exit');
  result = await resumeJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([result.code, result.state], [4, 'unknown_exit']);
  assert.equal(invocations(a), before, 'no replay in any window');
  assert.equal(receiptJob(a.job).code, 3, 'an unknown job has no receipt');
  // restored checkpoint resumes normally
  fs.writeFileSync(path.join(dir, 'exit.json'), keep.exit);
  fs.writeFileSync(path.join(dir, 'process.json'), keep.proc);
  fs.writeFileSync(path.join(a.job, 'job.json'), keep.job);
  assert.equal((await resumeJob(a.job, a.requestFile, deps(a))).state, 'returned');
});

test('a launch failure (missing executable) is NOT_RUN and is not retried or replayed', async () => {
  const a = area('launch-failed');
  write(a, { provider: { kind: 'codex', executable: path.join(a.dir, 'no-such-cli') } });
  const result = await startJob(a.job, a.requestFile, deps(a));
  assert.equal(result.state, 'launch_failed');
  assert.equal(readReceipt(a).status, 'NOT_RUN');
  assert.equal(jobJson(a).attempts.length, 1);
  assert.equal((await resumeJob(a.job, a.requestFile, deps(a))).code, 3);
});

// ------------------------------------------------------------------------------------------------------------ parser unit checks of the installed contract
test('observeCodex follows the installed run_codex.py contract: exact structured capacity only, one session, no open items', () => {
  const sid = SID;
  assert.equal(observeCodex(capacityEvents(sid), 1, null, 'm').outcome, 'capacity');
  assert.equal(observeCodex(capacityEvents(sid), 2, null, 'm').outcome, 'native_failure');
  assert.equal(observeCodex(capacityEvents(sid, CAPACITY + ' extra'), 1, null, 'm').outcome, 'native_failure');
  assert.equal(observeCodex(capacityEvents(sid), 1, OTHER_SID, 'm').outcome, 'session_unresolved');
  assert.equal(observeCodex(codex('not-a-uuid'), 0, null, 'm').outcome, 'invalid_events');
  assert.equal(observeCodex(codex(sid, { type: 'item.started', item: {} }), 0, null, 'm').outcome, 'invalid_events');
  assert.equal(observeCodex(codex(sid, done), 0, sid, 'm').outcome, 'native_success');
});

// ------------------------------------------------------------------------------------------------------------ separation from evaluation
test('the executor reports execution only: no evaluation receipt/checks, no Harness2 call, no other-skill dependency in its source', async () => {
  const full = fs.readFileSync(EXECUTOR, 'utf8');
  const source = full.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
  assert.ok(!/harness2/i.test(source), 'no Harness2 name outside comments');
  assert.ok(!/evaluation-receipt|evaluator_id|cli\.mjs|worker-agent-control|discover\.py|run_codex\.py/.test(source));
  assert.ok(!/shell:\s*true|\bexec\(|execSync|execFile/.test(source));
  assert.ok(!/ignore-user-config|dangerously/.test(source.replace(/const FORBIDDEN_TOKENS[\s\S]*?\]\);/, '')));
  const a = area('separation');
  plan(a, [{ stdout: codex(SID, done) }]);
  write(a);
  await startJob(a.job, a.requestFile, deps(a));
  const receipt = readReceipt(a);
  assertReceiptShape(receipt);
  assert.equal(Object.keys(receipt).some((k) => /check|evaluat|verdict/.test(k)), false, 'no evaluation member');
  assert.equal(buildReceipt(a.job, { ...jobJson(a), state: 'capacity_deferred' }, receipt.identity), null);
});

// ------------------------------------------------------------------------------------------------------------ CLI
test('CLI: run / status / receipt through a real process with exit codes and JSON output', () => {
  const a = area('cli');
  plan(a, [{ stdout: codex(SID, done) }]);
  write(a);
    const run = spawnSync(process.execPath, [EXECUTOR, 'run', a.job, '--request', a.requestFile], { encoding: 'utf8' });
  assert.equal(run.status, 0, run.stderr);
  const body = JSON.parse(run.stdout);
  assert.equal(body.state, 'returned');
  assert.equal(path.basename(body.receipt), 'receipt.json');
  const status = spawnSync(process.execPath, [EXECUTOR, 'status', a.job], { encoding: 'utf8' });
  assert.equal(status.status, 0);
  assert.equal(JSON.parse(status.stdout).state, 'returned');
  const again = spawnSync(process.execPath, [EXECUTOR, 'run', a.job, '--request', a.requestFile], { encoding: 'utf8' });
  assert.equal(again.status, 3);
  assert.equal(JSON.parse(again.stdout).state, 'duplicate_job');
  const invalid = spawnSync(process.execPath, [EXECUTOR, 'run', path.join(a.dir, 'other'), '--request', path.join(a.dir, 'missing.json')], { encoding: 'utf8' });
  assert.equal(invalid.status, 2);
  const cap = area('cli-capacity');
  plan(cap, [{ stdout: capacityEvents(SID), exit: 1 }]);
  write(cap, { budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 0, backoff_seconds: 0, burst_seconds: 100 } });
  const open = spawnSync(process.execPath, [EXECUTOR, 'run', cap.job, '--request', cap.requestFile], { encoding: 'utf8' });
  assert.equal(open.status, 75);
  assert.equal(JSON.parse(open.stdout).open, true);
  const noReceipt = spawnSync(process.execPath, [EXECUTOR, 'receipt', cap.job], { encoding: 'utf8' });
  assert.equal(noReceipt.status, 3);
});

// ====================================================================================================================
// Repair v2: N1 unspecified values, N2 model provenance, N3 wrapper errors, N4 ordered templates, N5 cancellation, wall accounting
// ====================================================================================================================
const CLAUDE_START = ['-p', '--output-format', 'json'];
const claudeProvider = (a, start = CLAUDE_START, resume = [...start, '--resume', '{session_id}']) => ({ kind: 'claude', executable: a.exe, start_argv: start, resume_argv: resume });
const stream = (...events) => events.map((e) => JSON.stringify(e)).join('\n') + '\n';
const init = (model, session = SID) => ({ type: 'system', subtype: 'init', session_id: session, model, tools: ['Read'] });
const assistant = (model, parent = null, session = SID) => ({
  type: 'assistant', session_id: session, parent_tool_use_id: parent,
  message: { model, content: [{ type: 'thinking', thinking: 'x', model: 'ignored-thinking-model' }, { type: 'tool_use', name: 'T', input: { model: 'ignored-tool-model' } }] },
});
const resultEvent = (extra = {}) => ({ type: 'result', subtype: 'success', is_error: false, result: 'ok', session_id: SID, ...extra });
const profileOf = (a, extra) => ({ ...requestOf(a).profile, ...extra });

test('N1: an unspecified effort/model is an explicit null: no flag is passed, nothing is invented, requested stays null and observed UNREPORTED', async () => {
  const a = area('null-effort');
  plan(a, [{ stdout: claudeWrapper() }]);
  // a caller-supplied route prefix (`launch`) and the model flag only; the effort is absent from BOTH templates
  const start = ['launch', '-p', '--output-format', 'json', '--model', '{model}'];
  write(a, { provider: claudeProvider(a, start), profile: profileOf(a, { effort: null }) });
  assert.equal((await startJob(a.job, a.requestFile, deps(a))).state, 'returned');
  assert.deepEqual(invocation(a, 1).argv, ['launch', '-p', '--output-format', 'json', '--model', 'model-fixture']);
  const job = jobJson(a);
  assert.equal(job.requested.effort, null);
  assert.equal(job.observed.effort, null);
  assert.match(readReceipt(a).note, /observed_effort=UNREPORTED/);
  assert.ok(!JSON.stringify(job).includes('UNSPECIFIED'));
  // both unspecified: the bare launcher invocation
  const b = area('null-both');
  plan(b, [{ stdout: claudeWrapper() }]);
  write(b, { provider: claudeProvider(b), profile: profileOf(b, { model: null, effort: null }) });
  assert.equal((await startJob(b.job, b.requestFile, deps(b))).state, 'returned');
  assert.deepEqual(invocation(b, 1).argv, ['-p', '--output-format', 'json']);
  assert.equal(jobJson(b).requested.model, null);
});

test('N1: missing key vs explicit null; null cannot hide a contradictory literal flag; codex still needs explicit model and effort; start and resume express the same selection', () => {
  const a = area('null-validation');
  const ok = (override) => assert.doesNotThrow(() => validateRequest(requestOf(a, override)));
  const bad = (override, pattern) => assert.throws(() => validateRequest(requestOf(a, override)), (e) => e instanceof InputError && pattern.test(e.message));
  const model = ['-p', '--model', '{model}'];
  ok({ provider: claudeProvider(a, model), profile: profileOf(a, { effort: null }) });
  ok({ provider: claudeProvider(a), profile: profileOf(a, { model: null, effort: null }) });
  const { effort, ...withoutEffort } = profileOf(a);
  bad({ provider: claudeProvider(a, model), profile: withoutEffort }, /profile keys must be exactly.*missing: effort/);
  bad({ provider: claudeProvider(a, [...model, '--effort', '{effort}']), profile: profileOf(a, { effort: null }) }, /profile\.effort is null/);
  bad({ provider: claudeProvider(a, [...model, '--effort', 'high']), profile: profileOf(a, { effort: null }) }, /must not carry an effort option/);
  bad({ provider: claudeProvider(a, [...model, '-c', 'model_reasoning_effort="high"']), profile: profileOf(a, { effort: null }) }, /must not carry an effort option/);
  bad({ provider: claudeProvider(a, ['-p', '--model', 'literal']), profile: profileOf(a, { model: null, effort: null }) }, /must not carry a model flag/);
  bad({ provider: claudeProvider(a, ['-p', '--model', '{model}']), profile: profileOf(a, { model: null, effort: null }) }, /profile\.model is null/);
  bad({ provider: claudeProvider(a, ['-p']), profile: profileOf(a, { effort: null }) }, /declared model/);
  bad({ profile: profileOf(a, { effort: null }) }, /profile\.effort must be an explicit string for the codex provider/);
  bad({ profile: profileOf(a, { model: null }) }, /profile\.model must be an explicit string for the codex provider/);
  bad({ provider: claudeProvider(a, model, [...model, '--effort', '{effort}', '--resume', '{session_id}']), profile: profileOf(a, { effort: null }) }, /profile\.effort is null|must equal the start template/);
  bad({ provider: claudeProvider(a, model, ['-p', '--resume', '{session_id}']), profile: profileOf(a, { effort: null }) }, /must equal the start template/);
  bad({ profile: profileOf(a, { effort: '' }) }, /profile\.effort must be a non-empty string or an explicit null/);
});

test('N2: alias != canonical name; helper/child/synthetic/thinking/tool/usage models are never primary; only an explicit expected set is judged', async () => {
  const provider = (a) => claudeProvider(a, ['-p', '--output-format', 'stream-json', '--verbose', '--model', '{model}']);
  const prof = (a, expected) => profileOf(a, { model: 'sonnet-alias', effort: null, expected_primary_models: expected });
  // alias requested, canonical reported by system.init and the main assistant message
  const a = area('alias');
  plan(a, [{ stdout: stream(init('claude-sonnet-5-5'), assistant('claude-sonnet-5-5'), resultEvent({ modelUsage: { 'claude-sonnet-5-5': {}, 'claude-haiku-old': {} } })) }]);
  write(a, { provider: provider(a), profile: prof(a, ['claude-sonnet-5-5']) });
  assert.equal((await startJob(a.job, a.requestFile, deps(a))).state, 'returned');
  let job = jobJson(a);
  assert.deepEqual(job.observed.models, ['claude-sonnet-5-5']);
  assert.deepEqual(job.observed.usage_models, ['claude-sonnet-5-5', 'claude-haiku-old']);
  assert.equal(job.observed.model_check, 'MATCH');
  assert.equal(job.requested.model, 'sonnet-alias');
  assert.ok(!job.observed.models.includes('sonnet-alias'), 'the request is never copied into observed');
  const native = JSON.parse(fs.readFileSync(path.join(a.job, 'attempts', 'attempt-0001', 'native.json'), 'utf8'));
  assert.equal(native.source_format, 'stream-json');
  assert.deepEqual(native.model_provenance.primary_models.map((m) => m.source), ['claude.stream.system.init.model', 'claude.stream.assistant.message.model(main)']);
  // child, synthetic, thinking and tool-payload models and aggregate usage: not primary, not a mismatch
  const b = area('helpers');
  plan(b, [{ stdout: stream(init('claude-sonnet-5-5'), assistant('claude-haiku-helper', 'toolu_child'), assistant('<synthetic>'), assistant('claude-sonnet-5-5'), resultEvent({ modelUsage: { 'claude-haiku-helper': {}, 'old-model': {}, 'claude-sonnet-5-5': {} } })) }]);
  write(b, { provider: provider(b), profile: prof(b, ['claude-sonnet-5-5']) });
  assert.equal((await startJob(b.job, b.requestFile, deps(b))).state, 'returned');
  job = jobJson(b);
  assert.deepEqual(job.observed.models, ['claude-sonnet-5-5']);
  assert.deepEqual(job.observed.child_models, ['claude-haiku-helper']);
  assert.deepEqual(job.observed.usage_models, ['claude-haiku-helper', 'old-model', 'claude-sonnet-5-5']);
  assert.ok(!JSON.stringify(job).includes('ignored-'), 'thinking/tool payload models are ignored');
  // usage-only wrapper: nothing is inferred, the wrapper succeeds as a native return and the check is UNVERIFIED
  const c = area('usage-only');
  plan(c, [{ stdout: claudeWrapper({ modelUsage: { 'something-else': {} } }) }]);
  write(c, { provider: provider(c), profile: prof(c, ['claude-sonnet-5-5']) });
  assert.equal((await startJob(c.job, c.requestFile, deps(c))).state, 'returned');
  assert.deepEqual([jobJson(c).observed.models, jobJson(c).observed.usage_models, jobJson(c).observed.model_check], [[], ['something-else'], 'UNVERIFIED']);
  assert.match(readReceipt(c).note, /model_check=UNVERIFIED/);
  // nothing reported and no expectation
  const d = area('unknown');
  plan(d, [{ stdout: claudeWrapper() }]);
  write(d, { provider: provider(d), profile: prof(d, null) });
  assert.equal((await startJob(d.job, d.requestFile, deps(d))).state, 'returned');
  assert.equal(jobJson(d).observed.model_check, 'NO_EXPECTATION');
  // a deliberate primary outside the explicit set: identity_mismatch, FAIL, raw kept, no retry
  const e = area('mismatch');
  plan(e, [{ stdout: stream(init('claude-sonnet-5-5'), assistant('some-other-primary'), resultEvent()) }, { stdout: stream(init('claude-sonnet-5-5'), resultEvent()) }]);
  write(e, { provider: provider(e), profile: prof(e, ['claude-sonnet-5-5']) });
  assert.equal((await startJob(e.job, e.requestFile, deps(e))).state, 'identity_mismatch');
  assert.equal(readReceipt(e).status, 'FAIL');
  assert.equal(invocations(e), 1);
  assert.deepEqual(jobJson(e).observed.models, ['claude-sonnet-5-5', 'some-other-primary']);
  // validation of the expectation itself
  const f = area('expected-validation');
  const bad = (expected, pattern) => assert.throws(() => validateRequest(requestOf(f, { profile: profileOf(f, { expected_primary_models: expected }) })), (err) => err instanceof InputError && pattern.test(err.message));
  bad([], /expected_primary_models/);
  bad(['a', 'a'], /expected_primary_models/);
  bad('a', /expected_primary_models/);
  assert.throws(() => validateRequest(requestOf(f, { profile: (({ expected_primary_models, ...rest }) => rest)(profileOf(f)) })), /profile keys must be exactly/);
});

test('N2: stream-json session reconciliation and a required terminal result; wrapper models stay usage-only', () => {
  const good = stream(init('m'), assistant('m'), resultEvent());
  assert.equal(observeClaude(good, 0, null).outcome, 'native_success');
  assert.equal(observeClaude(good, 0, OTHER_SID).outcome, 'session_unresolved');
  assert.equal(observeClaude(stream(init('m'), resultEvent({ session_id: OTHER_SID })), 0, null).outcome, 'session_unresolved');
  assert.equal(observeClaude(stream(init('m'), assistant('m', null, 'not-a-uuid'), resultEvent()), 0, null).outcome, 'session_unresolved');
  assert.equal(observeClaude(stream(init('m'), assistant('m')), 0, null).outcome, 'invalid_events', 'no terminal result');
  assert.equal(observeClaude('{"type":"result"}\nnot json\n', 0, null).outcome, 'invalid_events');
  const wrapper = observeClaude(claudeWrapper({ modelUsage: { x: {} } }), 0, null);
  assert.deepEqual([wrapper.primary, wrapper.usage_models, wrapper.format], [[], ['x'], 'wrapper']);
});

test('N3: reported errors reject a nominal success; malformed errors are invalid; the genuine max-turns envelope is a budget checkpoint; precedence is kept', async () => {
  const wrapperProvider = (a) => ({ provider: claudeProvider(a, ['-p', '--model', '{model}', '--effort', '{effort}']) });
  const cases = [
    ['errors-on-success', claudeWrapper({ errors: ['something failed'] }), 0, 'native_error'],
    ['errors-string', claudeWrapper({ errors: 'boom' }), 0, 'invalid_events'],
    ['errors-null', claudeWrapper({ errors: null }), 0, 'invalid_events'],
    ['errors-object', claudeWrapper({ errors: { a: 1 } }), 0, 'invalid_events'],
    ['genuine-max-turns', claudeWrapper({ subtype: 'error_max_turns', is_error: true, errors: ['Reached maximum number of turns (3)'], result: undefined }), 1, 'budget_exhausted'],
    ['denial-outranks-budget', claudeWrapper({ subtype: 'error_max_turns', is_error: true, permission_denials: [{ tool_name: 'Bash' }], errors: ['x'] }), 1, 'permission_denied'],
    ['bad-session-with-errors', claudeWrapper({ session_id: 'nope', errors: ['x'] }), 0, 'session_unresolved'],
    ['stream-errors', stream(init('m'), resultEvent({ errors: ['late error'] })), 0, 'native_error'],
  ];
  for (const [name, stdout, exit, state] of cases) {
    const a = area('n3-' + name);
    plan(a, [{ stdout, exit }]);
    write(a, wrapperProvider(a));
    assert.equal((await startJob(a.job, a.requestFile, deps(a))).state, state, name);
    assert.equal(invocations(a), 1, `${name}: not retried`);
    const open = ['budget_exhausted'].includes(state);
    assert.equal(fs.existsSync(path.join(a.job, 'receipt.json')), !open, name);
    if (!open) assert.equal(readReceipt(a).status, 'FAIL');
  }
  const a = area('n3-resume-budget');
  plan(a, [{ stdout: claudeWrapper({ subtype: 'error_max_turns', is_error: true, errors: ['Reached maximum number of turns (3)'] }), exit: 1 }, { stdout: claudeWrapper() }]);
  write(a, wrapperProvider(a));
  assert.equal((await startJob(a.job, a.requestFile, deps(a))).state, 'budget_exhausted');
  assert.equal((await resumeJob(a.job, a.requestFile, deps(a))).state, 'returned');
});

test('N4: ordered template structure: swapped permission values, reordered/repeated flags and extra tokens are refused; real resume forms are accepted', async () => {
  const base = ['-p', '--output-format', 'json', '--allowedTools', 'Read', '--disallowedTools', 'Bash', '--model', '{model}', '--effort', '{effort}'];
  assert.ok(resumeIsStartPlusSession(base, [...base, '--resume', '{session_id}']));
  assert.ok(resumeIsStartPlusSession(base, [...base, '-r', '{session_id}']));
  assert.ok(resumeIsStartPlusSession(base, [...base, '--resume={session_id}']));
  assert.ok(resumeIsStartPlusSession(base, ['-p', '--resume', '{session_id}', ...base.slice(1)]), 'insertion elsewhere keeps the rest ordered');
  const codexStart = ['exec', '--json', '-s', 'read-only', '-m', '{model}', '-c', 'model_reasoning_effort={effort_json}', '-'];
  assert.ok(resumeIsStartPlusSession(codexStart, ['exec', 'resume', '--json', '-s', 'read-only', '-m', '{model}', '-c', 'model_reasoning_effort={effort_json}', '{session_id}', '-']));
  // swapped values: the SAME multiset of tokens, different flag-value association
  const swapped = ['-p', '--output-format', 'json', '--allowedTools', 'Bash', '--disallowedTools', 'Read', '--model', '{model}', '--effort', '{effort}'];
  assert.ok(!resumeIsStartPlusSession(base, [...swapped, '--resume', '{session_id}']));
  assert.ok(!resumeIsStartPlusSession(base, ['-p', '--output-format', 'json', '--disallowedTools', 'Bash', '--allowedTools', 'Read', '--model', '{model}', '--effort', '{effort}', '--resume', '{session_id}']), 'reordered flags');
  const repeated = ['--allowedTools', 'Read', '--allowedTools', 'Edit', '--model', '{model}', '--effort', '{effort}'];
  assert.ok(resumeIsStartPlusSession(repeated, [...repeated, '--resume', '{session_id}']));
  assert.ok(!resumeIsStartPlusSession(repeated, ['--allowedTools', 'Edit', '--allowedTools', 'Read', '--model', '{model}', '--effort', '{effort}', '--resume', '{session_id}']), 'repeated flag order');
  assert.ok(!resumeIsStartPlusSession(base, [...base, '--resume', '{session_id}', '--allowedTools', 'Bash']), 'extra permission flag');
  assert.ok(!resumeIsStartPlusSession(base, [...base, 'resume']), 'verb without session');
  assert.ok(!resumeIsStartPlusSession(base, [...base, '--resume', 'x', '{session_id}']), 'flag and session not adjacent');
  assert.ok(!resumeIsStartPlusSession(base, [...base, '--resume', '{session_id}', '--resume', '{session_id}']), 'resume twice');
  assert.ok(!resumeIsStartPlusSession(base, base), 'no resume at all');
  // end to end through validateRequest, and a changed-but-consistent permission set on resume spawns nothing
  const a = area('ordered');
  const prov = (start, resume) => ({ kind: 'claude', executable: a.exe, start_argv: start, resume_argv: resume });
  assert.throws(() => validateRequest(requestOf(a, { provider: prov(base, [...swapped, '--resume', '{session_id}']) })), (e) => e instanceof InputError && /must equal the start template/.test(e.message));
  plan(a, [{ stdout: claudeWrapper({ subtype: 'error_max_turns', is_error: true, errors: ['Reached maximum number of turns (1)'] }), exit: 1 }, { stdout: claudeWrapper() }]);
  const original = write(a, { provider: prov(base, [...base, '--resume', '{session_id}']) });
  assert.equal((await startJob(a.job, a.requestFile, deps(a))).state, 'budget_exhausted');
  const before = invocations(a);
  write(a, { ...original, provider: prov(swapped, [...swapped, '--resume', '{session_id}']) });
  const changed = await resumeJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([changed.code, changed.state], [2, 'changed_input']);
  write(a, { ...original, provider: prov(base, [...swapped, '--resume', '{session_id}']) });
  await assert.rejects(resumeJob(a.job, a.requestFile, deps(a)), InputError);
  assert.equal(invocations(a), before, 'nothing spawned');
});

test('N5: cancellation before the first launch, during backoff and between attempts never starts another child; an aborted wait is measured, not charged as planned', async () => {
  // before the first launch
  const pre = area('cancel-pre');
  plan(pre, [{ stdout: codex(SID, done) }]);
  write(pre);
  const aborted = new AbortController();
  aborted.abort();
  const first = await startJob(pre.job, pre.requestFile, deps(pre), aborted.signal);
  assert.deepEqual([first.code, first.state, first.attempts], [1, 'canceled', 0]);
  assert.equal(invocations(pre), 0);
  assert.equal(readReceipt(pre).status, 'NOT_RUN');
  assert.equal(jobJson(pre).cancel.phase, 'before_launch');
  // during a (stubbed) backoff: the wait ends, the abort is seen, no second child
  const mid = area('cancel-backoff');
  plan(mid, [{ stdout: capacityEvents(SID), exit: 1 }, { stdout: codex(SID, done) }]);
  write(mid, { budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 2, backoff_seconds: 3, burst_seconds: 200 } });
  const controller = new AbortController();
  const stubbed = { ...deps(mid), sleep: async (ms) => { mid.delays.push(ms); controller.abort(); } };
  const second = await startJob(mid.job, mid.requestFile, stubbed, controller.signal);
  assert.deepEqual([second.state, second.attempts], ['canceled', 1]);
  assert.equal(invocations(mid), 1, 'no second child after the abort');
  assert.equal(readReceipt(mid).status, 'FAIL');
  assert.equal(jobJson(mid).cancel.phase, 'backoff');
  assert.equal(jobJson(mid).bursts[0].backoffs[0].aborted, true);
  assert.equal((await resumeJob(mid.job, mid.requestFile, deps(mid))).code, 3, 'a canceled job is not auto-resumed');
  // between attempts (no backoff): the abort arrives after the first attempt, before the next spawn
  const between = area('cancel-between');
  plan(between, [{ stdout: capacityEvents(SID), exit: 1 }, { stdout: codex(SID, done) }]);
  write(between, { budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 2, backoff_seconds: 0, burst_seconds: 200 } });
  const c2 = new AbortController();
  let calls = 0;
  const trap = { sleep: async () => {}, now: () => { calls += 1; if (calls === 3) c2.abort(); return 0; } };
  const third = await startJob(between.job, between.requestFile, trap, c2.signal);
  assert.deepEqual([third.state, third.attempts], ['canceled', 1]);
  assert.equal(invocations(between), 1);
  assert.equal(jobJson(between).cancel.phase, 'between_attempts');
  // a REAL wait interrupted by a real abort: observed ~0.3 s of a 5 s backoff
  const real = area('cancel-real-wait');
  plan(real, [{ stdout: capacityEvents(SID), exit: 1 }, { stdout: codex(SID, done) }]);
  write(real, { budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 2, backoff_seconds: 5, burst_seconds: 200 } });
  const c3 = new AbortController();
  const began = Date.now();
  const timer = setTimeout(() => c3.abort(), 1500);
  const fourth = await startJob(real.job, real.requestFile, undefined, c3.signal);
  clearTimeout(timer);
  assert.equal(fourth.state, 'canceled');
  assert.ok(Date.now() - began < 4000, 'the wait was interrupted');
  const burst = jobJson(real).bursts[0];
  assert.equal(burst.backoffs[0].planned_ms, 5000);
  assert.ok(burst.backoffs[0].observed_ms >= 200 && burst.backoffs[0].observed_ms < 3000, `observed ${burst.backoffs[0].observed_ms}`);
  assert.equal(invocations(real), 1);
  assert.ok(readReceipt(real).metrics.wall_ms < 4000, 'the planned wait is not charged');
  // an active child is stopped by the abort (process group), state canceled
  const act = area('cancel-active');
  plan(act, [{ stdout: codex(SID), sleep_ms: 30000 }]);
  write(act);
  const c4 = new AbortController();
  const t4 = setTimeout(() => c4.abort(), 500);
  const started = Date.now();
  const fifth = await startJob(act.job, act.requestFile, undefined, c4.signal);
  clearTimeout(t4);
  assert.equal(fifth.state, 'canceled');
  assert.ok(Date.now() - started < 15000);
  assert.equal(jobJson(act).cancel.phase, 'active_attempt');
  assert.equal(JSON.parse(fs.readFileSync(path.join(act.job, 'attempts', 'attempt-0001', 'exit.json'), 'utf8')).interrupted, 'canceled');
  assert.equal(readReceipt(act).status, 'FAIL');
});

test('N5: SIGTERM to the real CLI process during a backoff cancels the job without another child', async () => {
  const a = area('cancel-cli');
  plan(a, [{ stdout: capacityEvents(SID), exit: 1 }, { stdout: codex(SID, done) }]);
  write(a, { budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 2, backoff_seconds: 30, burst_seconds: 200 } });
  const child = spawn(process.execPath, [EXECUTOR, 'run', a.job, '--request', a.requestFile], { stdio: ['ignore', 'pipe', 'pipe'] });
  let out = '';
  child.stdout.on('data', (d) => { out += d; });
  const exited = new Promise((resolve) => child.once('close', (code) => resolve(code)));
  const limit = Date.now() + 10000;
  while (Date.now() < limit) {
    try { if (jobJson(a).next_delay_seconds === 30) break; } catch { /* not yet */ }
    await new Promise((r) => setTimeout(r, 50));
  }
  child.kill('SIGTERM');
  assert.equal(await exited, 1);
  assert.equal(JSON.parse(out).state, 'canceled');
  assert.equal(invocations(a), 1);
});

test('wall accounting: public wall_ms is the observed burst wall incl. a real backoff and local overhead; native active time and backoff are stored separately; nothing else is promoted', async () => {
  const a = area('wall');
  plan(a, [{ stdout: capacityEvents(SID), exit: 1, sleep_ms: 100 }, { stdout: codex(SID, done), sleep_ms: 100 }]);
  write(a, { budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 1, backoff_seconds: 1, burst_seconds: 100 } });
  const began = Date.now();
  const result = await startJob(a.job, a.requestFile);
  const elapsed = Date.now() - began;
  assert.equal(result.state, 'returned');
  const job = jobJson(a);
  const burst = job.bursts[0];
  assert.equal(job.bursts.length, 1);
  assert.ok(burst.backoff_observed_ms >= 950 && burst.backoffs[0].aborted === false, `backoff ${burst.backoff_observed_ms}`);
  assert.ok(burst.native_active_ms >= 150, 'two fake attempts of >= 100 ms each are active native time');
  assert.ok(Math.abs(burst.wall_ms - (burst.native_active_ms + burst.backoff_observed_ms + burst.overhead_ms)) < 1.5, 'wall = native + backoff + overhead, nothing counted twice');
  assert.ok(burst.wall_ms >= burst.native_active_ms + burst.backoff_observed_ms - 1);
  assert.ok(burst.wall_ms <= elapsed + 5, 'not more than the real elapsed time');
  const receipt = readReceipt(a);
  assert.equal(receipt.metrics.wall_ms, burst.wall_ms);
  assert.ok(receipt.metrics.wall_ms >= 1100, 'the public wall includes the observed backoff, not only the active native time');
  assert.deepEqual([receipt.metrics.compute_ms, receipt.metrics.cost_usd, receipt.metrics.input_tokens, receipt.metrics.output_tokens], [null, null, null, null]);
  assert.match(receipt.note, /gaps between bursts and unfinished bursts are not observed and not charged/);
  for (const attempt of job.attempts) {
    assert.equal(typeof attempt.native_active_ms, 'number');
    assert.equal('wall_ms' in attempt, false, 'an attempt carries native_active_ms, never a mislabeled wall_ms');
  }
  assert.equal(job.attempts.reduce((sum, x) => sum + x.native_active_ms, 0) > 0, true);
});

// ====================================================================================================================
// Worker question relay: input_required stop, verbatim answer turn on the SAME native session, denials as events
// ====================================================================================================================
const askLine = (id = 'q1', prompt = 'Approve rebuild with vLLM 0.34.0?', blocking = true) =>
  JSON.stringify({ worker_status: 'input-required', question: { id, prompt, blocking } });
const answerOf = (extra = {}) => ({ question_id: 'q1', question: 'Approve rebuild with vLLM 0.34.0?', answer: '승인. 0.34.0으로 빌드해.', answered_utc: '2026-11-18T00:00:00Z', source: 'user', ...extra });
const writeAnswer = (a, value, name = 'answer.json') => { const file = path.join(a.dir, name); fs.writeFileSync(file, JSON.stringify(value)); return file; };

test('Q1: a returned worker that ends with an input-required line stops OPEN (75, no receipt) and reports the question; prose is never parsed', async () => {
  const a = area('q1-ask');
  plan(a, [{ stdout: claudeWrapper({ result: `I need a decision.\n${askLine()}\n` }) }]);
  write(a, { provider: claudeProvider(a), profile: profileOf(a, { model: null, effort: null }) });
  const first = await startJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([first.code, first.state, first.open, first.receipt], [75, 'input_required', true, null]);
  assert.equal(first.question.id, 'q1');
  assert.equal(first.question.blocking, true);
  const status = statusJob(a.job);
  assert.equal(status.resume_needs_answer, true);
  assert.equal(status.question.prompt, 'Approve rebuild with vLLM 0.34.0?');
  assert.equal(fs.existsSync(path.join(a.job, 'receipt.json')), false);
  const prose = area('q1-prose');
  plan(prose, [{ stdout: claudeWrapper({ result: 'status: input-required — should I continue? (prose only)' }) }]);
  write(prose, { provider: claudeProvider(prose), profile: profileOf(prose, { model: null, effort: null }) });
  assert.equal((await startJob(prose.job, prose.requestFile, deps(prose))).state, 'returned');
  const other = area('q1-other-status');
  plan(other, [{ stdout: claudeWrapper({ result: JSON.stringify({ worker_status: 'completed' }) }) }]);
  write(other, { provider: claudeProvider(other), profile: profileOf(other, { model: null, effort: null }) });
  assert.equal((await startJob(other.job, other.requestFile, deps(other))).state, 'returned');
});

test('Q2: a malformed input-required line is a terminal invalid_question, never a guessed question', async () => {
  const a = area('q2-bad');
  plan(a, [{ stdout: claudeWrapper({ result: JSON.stringify({ worker_status: 'input-required', question: { id: 'q1', prompt: '' } }) }) }]);
  write(a, { provider: claudeProvider(a), profile: profileOf(a, { model: null, effort: null }) });
  const result = await startJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([result.code, result.state], [1, 'invalid_question']);
  assert.equal(readReceipt(a).status, 'FAIL');
});

test('Q3: resume refuses a missing, mismatched, non-user or extra-key answer before spawning; an answer outside input_required is refused', async () => {
  const a = area('q3-refuse');
  plan(a, [{ stdout: claudeWrapper({ result: askLine() }) }, { stdout: claudeWrapper() }]);
  write(a, { provider: claudeProvider(a), profile: profileOf(a, { model: null, effort: null }) });
  await startJob(a.job, a.requestFile, deps(a));
  const none = await resumeJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([none.code, none.state], [2, 'answer_required']);
  for (const [name, bad, pattern] of [
    ['wrong-id', answerOf({ question_id: 'q9' }), /pending question id/],
    ['missing-question-text', answerOf({ question: '  ' }), /question must be a non-empty string/],
    ['not-user', answerOf({ source: 'orchestrator' }), /only a person/],
    ['empty', answerOf({ answer: '  ' }), /verbatim words/],
    ['local-time', answerOf({ answered_utc: '2026-11-18T09:00:00+09:00' }), /UTC/],
    ['extra-key', { ...answerOf(), approval: 'granted' }, /exactly/],
  ]) {
    await assert.rejects(resumeJob(a.job, a.requestFile, deps(a), undefined, writeAnswer(a, bad, name + '.json')), (e) => e instanceof InputError && pattern.test(e.message), name);
  }
  assert.equal(invocations(a), 1, 'nothing spawned by a refused answer');
  assert.equal(fs.existsSync(path.join(a.job, 'turns')), false);
  const b = area('q3-not-pending');
  plan(b, [{ stdout: claudeWrapper({ subtype: 'error_max_turns', is_error: true, errors: ['Reached maximum number of turns (3)'] }), exit: 1 }]);
  write(b, { provider: claudeProvider(b), profile: profileOf(b, { model: null, effort: null }) });
  await startJob(b.job, b.requestFile, deps(b));
  const wrong = await resumeJob(b.job, b.requestFile, deps(b), undefined, writeAnswer(b, answerOf()));
  assert.deepEqual([wrong.code, wrong.state], [2, 'budget_exhausted']);
});

test('Q4: a valid answer is frozen as turns/0001/answer.json and sent verbatim on the SAME session; the job then returns with a receipt', async () => {
  const a = area('q4-answer');
  plan(a, [{ stdout: claudeWrapper({ result: askLine() }) }, { stdout: claudeWrapper({ result: 'built and verified' }) }]);
  write(a, { provider: claudeProvider(a), profile: profileOf(a, { model: null, effort: null }) });
  await startJob(a.job, a.requestFile, deps(a));
  const answer = answerOf({ question: 'Rebuild with vLLM 0.34.0 — approve?' });
  const resumed = await resumeJob(a.job, a.requestFile, deps(a), undefined, writeAnswer(a, answer));
  assert.deepEqual([resumed.code, resumed.state], [0, 'returned']);
  const frozen = JSON.parse(fs.readFileSync(path.join(a.job, 'turns', '0001', 'answer.json'), 'utf8'));
  assert.deepEqual(frozen, { ...answer, question: answerOf().question });
  const second = invocation(a, 2);
  assert.ok(second.argv.includes('--resume') && second.argv.includes(SID), 'same native session');
  assert.ok(second.stdin.includes(answer.answer) && second.stdin.includes(answerOf().question), 'original question and user answer');
  assert.ok(!second.stdin.includes(answer.question), 'the worker receives its stored question');
  assert.ok(second.stdin.includes('does not widen any approval'), 'answer is not an approval');
  assert.ok(!second.stdin.includes('PUBLIC TASK PROMPT'), 'the frozen prompt is not replayed on an answer turn');
  const receipt = readReceipt(a);
  assert.equal(receipt.status, 'PASS');
  assert.ok(receipt.evidence.includes(path.join(a.job, 'turns', '0001', 'answer.json')));
  assert.match(receipt.note, /answers_recorded=1; answers_delivered=1/);
  await assert.rejects(resumeJob(a.job, a.requestFile, deps(a), undefined, writeAnswer(a, answer, 'again.json')).then((r) => { if (r.code === 3) throw new InputError('terminal'); }), InputError);
});

test('Q5: a capacity failure on the answer turn keeps the answer pending and resends it verbatim on the next same-session attempt', async () => {
  const a = area('q5-codex-capacity');
  plan(a, [
    { stdout: codex(SID, done), final: `need input\n${askLine()}\n` },
    { stdout: capacityEvents(SID), exit: 1 },
    { stdout: codex(SID, done), final: 'done' },
  ]);
  write(a, { budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 2, backoff_seconds: 0, burst_seconds: 100 } });
  const first = await startJob(a.job, a.requestFile, deps(a));
  assert.equal(first.state, 'input_required');
  const answer = answerOf();
  const resumed = await resumeJob(a.job, a.requestFile, deps(a), undefined, writeAnswer(a, answer));
  assert.deepEqual([resumed.code, resumed.state], [0, 'returned']);
  for (const n of [2, 3]) assert.ok(invocation(a, n).stdin.includes(answer.answer), `attempt ${n} carries the answer`);
  assert.equal(jobJson(a).turns[0].consumed_by_attempt, 3);
});

test('Q6: denials on a returned run are recorded as events and counted in the receipt note; a question with denials still stops to ask', async () => {
  const a = area('q6-denials');
  plan(a, [{ stdout: claudeWrapper({ permission_denials: [{ tool_name: 'Bash' }, { tool_name: 'Bash' }] }) }]);
  write(a, { provider: claudeProvider(a), profile: profileOf(a, { model: null, effort: null }) });
  const result = await startJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([result.code, result.state, result.permission_denials], [0, 'returned_with_denials', 2]);
  assert.match(readReceipt(a).note, /permission_denials=2/);
  const native = JSON.parse(fs.readFileSync(path.join(a.job, 'attempts', 'attempt-0001', 'native.json'), 'utf8'));
  assert.equal(native.permission_denials.length, 2);
  const b = area('q6-ask-denials');
  plan(b, [{ stdout: claudeWrapper({ result: askLine(), permission_denials: [{ tool_name: 'Bash' }] }) }]);
  write(b, { provider: claudeProvider(b), profile: profileOf(b, { model: null, effort: null }) });
  const asked = await startJob(b.job, b.requestFile, deps(b));
  assert.deepEqual([asked.code, asked.state, asked.permission_denials], [75, 'input_required', 1]);
});

test('Q7: the CLI refuses --answer on run and passes it on resume', () => {
  const a = area('q7-cli');
  plan(a, [{ stdout: claudeWrapper({ result: askLine() }) }, { stdout: claudeWrapper() }]);
  write(a, { provider: claudeProvider(a), profile: profileOf(a, { model: null, effort: null }) });
  const env = { ...process.env, FAKE_PLAN: path.join(a.dir, 'plan.json') };
  const run = spawnSync(process.execPath, [EXECUTOR, 'run', a.job, '--request', a.requestFile, '--answer', writeAnswer(a, answerOf())], { env, encoding: 'utf8' });
  assert.equal(run.status, 2);
  const start = spawnSync(process.execPath, [EXECUTOR, 'run', a.job, '--request', a.requestFile], { env, encoding: 'utf8' });
  assert.equal(start.status, 75);
  const resume = spawnSync(process.execPath, [EXECUTOR, 'resume', a.job, '--request', a.requestFile, '--answer', writeAnswer(a, answerOf(), 'cli-answer.json')], { env, encoding: 'utf8' });
  assert.equal(resume.status, 0, resume.stderr);
  assert.equal(JSON.parse(resume.stdout).state, 'returned');
});

// ------------------------------------------------------------------------------------------------------------ review round 1 (F1-F8)
const nullProfile = (a) => ({ provider: claudeProvider(a), profile: profileOf(a, { model: null, effort: null }) });

test('F1/F2: misspelled status, two questions, a question that is not last, and placeholders fail closed or are ignored; prose never asks', async () => {
  const q = { id: 'q1', prompt: 'Rebuild?', blocking: true };
  const cases = [
    ['underscore', JSON.stringify({ worker_status: 'input_required', question: q }), 'invalid_question'],
    ['upper', JSON.stringify({ worker_status: 'INPUT-REQUIRED', question: q }), 'invalid_question'],
    ['two', `${askLine('q1')}\n${askLine('q2')}`, 'invalid_question'],
    ['not-last', `${askLine()}\nAll done.`, 'invalid_question'],
    ['quoted-template', `format:\n${JSON.stringify({ worker_status: 'input-required', question: { id: '<짧은 고유 id>', prompt: '<질문 원문>', blocking: true } })}\nAll tasks complete, nothing to ask.`, 'returned'],
    ['template-then-real', `${JSON.stringify({ worker_status: 'input-required', question: { id: '<id>', prompt: '<p>', blocking: true } })}\n${askLine()}`, 'input_required'],
  ];
  for (const [name, result, state] of cases) {
    const a = area('f1-' + name);
    plan(a, [{ stdout: claudeWrapper({ result }) }]);
    write(a, nullProfile(a));
    assert.equal((await startJob(a.job, a.requestFile, deps(a))).state, state, name);
  }
});

test('F1: codex without the -o file still sees the question from the last agent_message item', async () => {
  const a = area('f1-codex-agent-message');
  plan(a, [{ stdout: codex(SID, { type: 'item.started', item: { id: 'm1', type: 'agent_message' } }, { type: 'item.completed', item: { id: 'm1', type: 'agent_message', text: `need\n${askLine()}` } }, done) }]);
  write(a, { provider: { kind: 'codex', executable: a.exe, start_argv: ['exec', '--json', '-m', '{model}', '-c', 'model_reasoning_effort={effort_json}', '-'], resume_argv: ['exec', 'resume', '--json', '-m', '{model}', '-c', 'model_reasoning_effort={effort_json}', '{session_id}', '-'] } });
  const result = await startJob(a.job, a.requestFile, deps(a));
  assert.deepEqual([result.code, result.state, result.permission_denials], [75, 'input_required', null]);
});

test('F7: a question line in a non-success result is ignored; the result keeps its failure state', async () => {
  for (const [name, stdout, exit, state] of [
    ['native-error', claudeWrapper({ is_error: true, subtype: 'error_during_execution', result: askLine() }), 0, 'native_error'],
    ['max-turns', claudeWrapper({ subtype: 'error_max_turns', is_error: true, errors: ['Reached maximum number of turns (3)'], result: askLine() }), 1, 'budget_exhausted'],
  ]) {
    const a = area('f7-' + name);
    plan(a, [{ stdout, exit }]);
    write(a, nullProfile(a));
    assert.equal((await startJob(a.job, a.requestFile, deps(a))).state, state, name);
  }
});

test('F3/F7/R4: capacity exhaustion on the answer turn keeps the answer recorded; --answer is then refused and a plain resume resends it after the normal capacity backoff; the first answer delivery does not wait', async () => {
  const a = area('f3-capacity-exhausted');
  plan(a, [
    { stdout: codex(SID, done), final: askLine() },
    { stdout: capacityEvents(SID), exit: 1 },
    { stdout: codex(SID, done), final: 'done' },
  ]);
  write(a, { budget: { attempt_timeout_seconds: 20, additional_capacity_attempts: 0, backoff_seconds: 7, burst_seconds: 100 } });
  await startJob(a.job, a.requestFile, deps(a));
  const answer = answerOf();
  const first = await resumeJob(a.job, a.requestFile, deps(a), undefined, writeAnswer(a, answer));
  assert.equal(first.state, 'capacity_deferred');
  assert.deepEqual(a.delays, [], 'the answer turn did not wait for a capacity backoff');
  assert.equal(statusJob(a.job).answer_recorded, true);
  const refused = await resumeJob(a.job, a.requestFile, deps(a), undefined, writeAnswer(a, answer, 'again.json'));
  assert.deepEqual([refused.code, refused.state], [2, 'answer_recorded']);
  const plain = await resumeJob(a.job, a.requestFile, deps(a));
  assert.equal(plain.state, 'returned');
  assert.deepEqual(a.delays, [7000], 'the resend after capacity follows the capacity backoff');
  assert.ok(invocation(a, 3).stdin.includes(answer.answer), 'resent verbatim');
});

test('F3: an orphan frozen answer with the same content is adopted; a different one is refused, never overwritten', async () => {
  const a = area('f3-orphan');
  plan(a, [{ stdout: claudeWrapper({ result: askLine() }) }, { stdout: claudeWrapper() }]);
  write(a, nullProfile(a));
  await startJob(a.job, a.requestFile, deps(a));
  const dir = path.join(a.job, 'turns', '0001');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'answer.json'), JSON.stringify(answerOf({ answer: 'different words' })));
  await assert.rejects(resumeJob(a.job, a.requestFile, deps(a), undefined, writeAnswer(a, answerOf())), (e) => e instanceof InputError && /already frozen/.test(e.message));
  fs.writeFileSync(path.join(dir, 'answer.json'), JSON.stringify(answerOf()));
  assert.equal((await resumeJob(a.job, a.requestFile, deps(a), undefined, writeAnswer(a, answerOf(), 'same.json'))).state, 'returned');
});

test('F4: denial counts are cumulative over the job and drive the terminal state; codex denials are UNREPORTED, not 0', async () => {
  const a = area('f4-cumulative');
  plan(a, [{ stdout: claudeWrapper({ result: askLine(), permission_denials: [{ tool_name: 'Bash' }] }) }, { stdout: claudeWrapper() }]);
  write(a, nullProfile(a));
  await startJob(a.job, a.requestFile, deps(a));
  const done2 = await resumeJob(a.job, a.requestFile, deps(a), undefined, writeAnswer(a, answerOf()));
  assert.deepEqual([done2.state, done2.permission_denials], ['returned_with_denials', 1]);
  const c = area('f4-codex');
  plan(c, [{ stdout: codex(SID, done) }]);
  write(c);
  const r = await startJob(c.job, c.requestFile, deps(c));
  assert.equal(r.permission_denials, null);
  assert.match(readReceipt(c).note, /permission_denials=UNREPORTED/);
});

test('F7: an old job.json without turns/denials still reports status and resumes from a budget checkpoint', async () => {
  const a = area('f7-old-job');
  plan(a, [{ stdout: claudeWrapper({ subtype: 'error_max_turns', is_error: true, errors: ['Reached maximum number of turns (3)'] }), exit: 1 }, { stdout: claudeWrapper() }]);
  write(a, nullProfile(a));
  await startJob(a.job, a.requestFile, deps(a));
  const file = path.join(a.job, 'job.json');
  const old = JSON.parse(fs.readFileSync(file, 'utf8'));
  delete old.turns;
  delete old.denials;
  fs.writeFileSync(file, JSON.stringify(old));
  assert.equal(statusJob(a.job).state, 'budget_exhausted');
  assert.equal((await resumeJob(a.job, a.requestFile, deps(a))).state, 'returned');
});

test('F8: answered_utc must be a full ISO UTC timestamp', async () => {
  const a = area('f8-utc');
  plan(a, [{ stdout: claudeWrapper({ result: askLine() }) }]);
  write(a, nullProfile(a));
  await startJob(a.job, a.requestFile, deps(a));
  for (const bad of ['2026Z', '2026-11-18Z', '2026-11-18T00:00Z']) {
    await assert.rejects(resumeJob(a.job, a.requestFile, deps(a), undefined, writeAnswer(a, answerOf({ answered_utc: bad }), `utc-${bad}.json`)), /YYYY-MM-DDTHH:MM:SS/, bad);
  }
});

// ------------------------------------------------------------------------------------------------------------ review round 2 (R1-R6)
test('R1: near-miss question forms are invalid_question, never a silent PASS; a trailing fence line is tolerated', async () => {
  const q = { id: 'q1', prompt: 'Rebuild?', blocking: true };
  const exact = askLine();
  const cases = [
    ['space-status', JSON.stringify({ worker_status: 'input required', question: q }), 'invalid_question'],
    ['dot-status', JSON.stringify({ worker_status: 'input-required.', question: q }), 'invalid_question'],
    ['unicode-hyphen', JSON.stringify({ worker_status: 'input‐required', question: q }), 'invalid_question'],
    ['other-status-with-question', JSON.stringify({ worker_status: 'needs-input', question: q }), 'invalid_question'],
    ['backticks', '`' + exact + '`', 'invalid_question'],
    ['prefix', 'QUESTION: ' + exact, 'invalid_question'],
    ['pretty-printed', JSON.stringify({ worker_status: 'input-required', question: q }, null, 2), 'invalid_question'],
    ['placeholder-last', JSON.stringify({ worker_status: 'input-required', question: { id: 'q1', prompt: '<질문 원문>', blocking: true } }), 'invalid_question'],
    ['trailing-sentence', exact + '\n답을 기다리겠습니다.', 'invalid_question'],
    ['fenced-at-end', '```json\n' + exact + '\n```', 'input_required'],
    ['plain-prose', 'I could ask for input, but everything is complete.', 'returned'],
  ];
  for (const [name, result, state] of cases) {
    const a = area('r1-' + name);
    plan(a, [{ stdout: claudeWrapper({ result }) }]);
    write(a, nullProfile(a));
    const r = await startJob(a.job, a.requestFile, deps(a));
    assert.equal(r.state, state, name);
    if (state === 'invalid_question') assert.equal(readReceipt(a).status, 'FAIL', name);
  }
});

test('R6: a question with keys other than id/prompt/blocking is invalid (facts and options go inside prompt)', async () => {
  const a = area('r6-extra-key');
  plan(a, [{ stdout: claudeWrapper({ result: JSON.stringify({ worker_status: 'input-required', question: { id: 'q1', prompt: 'Rebuild?', blocking: true, options: ['a', 'b'] } }) }) }]);
  write(a, nullProfile(a));
  assert.equal((await startJob(a.job, a.requestFile, deps(a))).state, 'invalid_question');
});

test('R3/R5: the answer block limits approval transcription to the asked action; the freeze is atomic and an unreadable frozen file is refused with its own reason', async () => {
  const a = area('r5-freeze');
  plan(a, [{ stdout: claudeWrapper({ result: askLine() }) }, { stdout: claudeWrapper() }]);
  write(a, nullProfile(a));
  await startJob(a.job, a.requestFile, deps(a));
  const dir = path.join(a.job, 'turns', '0001');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'answer.json'), '{"question_id": "q1", "quest');
  await assert.rejects(resumeJob(a.job, a.requestFile, deps(a), undefined, writeAnswer(a, answerOf())), (e) => e instanceof InputError && /unreadable/.test(e.message));
  fs.rmSync(path.join(dir, 'answer.json'));
  assert.equal((await resumeJob(a.job, a.requestFile, deps(a), undefined, writeAnswer(a, answerOf(), 'ok.json'))).state, 'returned');
  assert.deepEqual(fs.readdirSync(dir), ['answer.json'], 'no temporary file left behind');
  assert.ok(invocation(a, 2).stdin.includes('Record an approval only if these words themselves approve the specific action you asked about'));
});

// ------------------------------------------------------------------------------------------------------------ post-review (round 3 non-blocking)
test('P1: half-placeholder questions, status-key variants and bare question objects are attempts (invalid), not a silent PASS', async () => {
  const q = { id: 'q1', prompt: 'Rebuild?', blocking: true };
  const cases = [
    ['half-placeholder-not-last', JSON.stringify({ worker_status: 'input-required', question: { id: '<짧은 고유 id>', prompt: 'DB 마이그레이션 전에 A/B 중 무엇을 쓸까요?', blocking: true } }) + '\n답을 기다리겠습니다.', 'invalid_question'],
    ['camel-key', JSON.stringify({ workerStatus: 'input-required', question: q }), 'invalid_question'],
    ['multiline-camel-key', JSON.stringify({ workerStatus: 'input-required', question: q }, null, 2), 'invalid_question'],
    ['prefixed-hyphen-key', 'Pending: ' + JSON.stringify({ 'worker-status': 'input-required', question: q }), 'invalid_question'],
    ['prefixed-plus-valid', 'Pending: ' + askLine('earlier') + '\n' + askLine(), 'invalid_question'],
    ['dash-key', JSON.stringify({ 'worker-status': 'input-required', question: q }), 'invalid_question'],
    ['status-key', JSON.stringify({ status: 'input-required', question: q }), 'invalid_question'],
    ['bare-question', JSON.stringify({ question: q }), 'invalid_question'],
    ['unrelated-json', JSON.stringify({ status: 'ok', files: 3 }), 'returned'],
  ];
  for (const [name, result, state] of cases) {
    const a = area('p1-' + name);
    plan(a, [{ stdout: claudeWrapper({ result }) }]);
    write(a, nullProfile(a));
    assert.equal((await startJob(a.job, a.requestFile, deps(a))).state, state, name);
  }
});

test('P3: a launch failure on the answer turn is FAIL (work already ran), and the undelivered answer is recorded but not delivered', async () => {
  const a = area('p3-launch-fail');
  plan(a, [{ stdout: claudeWrapper({ result: askLine() }) }]);
  write(a, nullProfile(a));
  await startJob(a.job, a.requestFile, deps(a));
  fs.chmodSync(a.exe, 0o644);
  const r = await resumeJob(a.job, a.requestFile, deps(a), undefined, writeAnswer(a, answerOf()));
  assert.equal(r.state, 'launch_failed');
  const receipt = readReceipt(a);
  assert.equal(receipt.status, 'FAIL');
  assert.match(receipt.note, /answers_recorded=1; answers_delivered=0/);
});
