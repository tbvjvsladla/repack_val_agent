import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import * as api from '../src/experiment.mjs';

const cli = fileURLToPath(new URL('../src/cli.mjs', import.meta.url));

function fixture(t, overrides = {}) {
  const parent = path.resolve('temp');
  fs.mkdirSync(parent, { recursive: true });
  const dir = fs.mkdtempSync(path.join(parent, 'harness2-test-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const root = path.join(dir, 'run');
  const file = (name, content = name) => {
    const filename = path.join(dir, name);
    fs.writeFileSync(filename, content);
    return filename;
  };
  const contract = {
    task_id: 'task', experiment_id: 'experiment', evaluation_id: 'criteria-v1', source_id: 'source-v1',
    owner_id: 'main', evaluator_id: 'validator', goal: 'Preserve output and handoff', evidence_mode: 'fixture',
    scope: ['src', 'instructions.md'], required_checks: ['output', 'handoff'],
    evaluation_spec: file('evaluation-spec.json', JSON.stringify({ output: 'cells a,b in order', handoff: 'complete' })),
    cases: [{ id: 'normal', comparison_key: 'input-env-profile-boundary-v1' }],
    baseline: { id: 'base', artifact: file('baseline.patch') }, max_attempts: 20, ...overrides
  };
  api.init(root, contract);
  const propose = (value, extra = {}) => api.propose(root, {
    id: value, parents: ['base'], author_id: 'builder', artifact: file(value + '.patch'),
    hypothesis: 'Preserve the required contracts with less repeated work',
    changes: [{ path: 'src/parser.rs', description: 'Reuse parsed inputs' }], ...extra
  });
  const request = (value, candidate = 'base', caseId = 'normal') => api.request(root, { attempt_id: value, candidate_id: candidate, case_id: caseId });
  const observe = (r, extra = {}) => api.observe(root, {
    identity: r.identity, status: 'PASS', executor_id: 'worker', comparison_key: r.identity.comparison_key,
    metrics: { compute_ms: 10 }, evidence: [file(r.identity.attempt_id + '.execution.log')], ...extra
  });
  const evaluate = (r, extra = {}) => api.evaluate(root, {
    identity: r.identity, evaluator_id: 'validator', checks: { output: 'PASS', handoff: 'PASS' },
    evidence: [file(r.identity.attempt_id + '.evaluation.log')], ...extra
  });
  const complete = (value, candidate = 'base', options = {}) => {
    const r = request(value, candidate);
    observe(r, options.observation);
    evaluate(r, options.evaluation);
    return r;
  };
  const select = candidate => api.select(root, { owner_id: 'main', candidate_id: candidate, reason: 'Compare frozen requirements and actual receipts' });
  return { dir, root, file, contract, propose, request, observe, evaluate, complete, select };
}

test('whole CLI loop uses actual local executor and checker, composes then resumes', t => {
  const f = fixture(t);
  const run = (command, input) => {
    const args = [cli, command, f.root];
    if (input !== undefined) args.push(f.file('input.json', JSON.stringify(input)));
    return JSON.parse(execFileSync(process.execPath, args, { encoding: 'utf8' }));
  };
  const execute = (candidate, attempt) => {
    const request = run('request', { attempt_id: attempt, candidate_id: candidate, case_id: 'normal' });
    const raw = execFileSync(process.execPath, ['-e', 'process.stdout.write(JSON.stringify({cells:["a","b"],handoff:"complete"}))'], { encoding: 'utf8' });
    const output = f.file(attempt + '.json', raw);
    run('observe', { identity: request.identity, executor_id: 'local-fixture-worker', status: 'PASS',
      comparison_key: request.identity.comparison_key, evidence: [output], metrics: {} });
    const check = spawnSync(process.execPath, ['-e',
      'const a=JSON.parse(require("fs").readFileSync(process.argv[1],"utf8"));if(a.cells.join(",")!=="a,b"||a.handoff!=="complete")process.exit(1);process.stdout.write("output/handoff checked\\n")', output], { encoding: 'utf8' });
    assert.equal(check.status, 0);
    run('evaluate', { identity: request.identity, evaluator_id: 'validator', checks: { output: 'PASS', handoff: 'PASS' },
      evidence: [f.file(attempt + '.check.log', check.stdout)] });
  };
  f.propose('c1');
  execute('base', 'base-1');
  execute('c1', 'c1-1');
  run('contrast', { id: 'contrast-1', attempt_ids: ['base-1', 'c1-1'], observations: ['Both fixture outputs preserve the same output and handoff'] });
  run('propose', { id: 'combined', parents: ['base', 'c1'], author_id: 'builder', artifact: f.file('combined.patch'),
    hypothesis: 'Retain output with combined instructions', changes: [{ path: 'instructions.md', description: 'Combine observed guidance' }], contrast_ids: ['contrast-1'] });
  assert.equal(run('select', { owner_id: 'main', candidate_id: 'combined', reason: 'Must run composition first' }).status, 'WITHHELD');
  execute('combined', 'combined-1');
  assert.equal(run('select', { owner_id: 'main', candidate_id: 'combined', reason: 'All frozen fixture checks executed' }).status, 'SELECTED');
  const experience = run('reflect', { cue: 'Repeated parsing', lesson: 'This fixture preserves output', applicability: 'This fixture only',
    limitations: 'No target/model/campaign performance evaluated', attempt_ids: ['combined-1'] });
  assert.equal(experience.promotion, 'HOST_REVIEW_REQUIRED');
  const status = run('resume', f.contract);
  assert.equal(status.attempted, 3);
  assert.equal(status.selection.current_status, 'SELECTED');
  assert.equal(status.performance_claim_allowed, false);
  assert.equal(status.selection.comparisons[0].compute_ratio, null);
});

test('a fast failing candidate cannot win, retry preserves all attempts and measured cost', t => {
  const f = fixture(t);
  f.propose('fast');
  f.complete('base-1');
  f.complete('fast-1', 'fast', { observation: { metrics: { compute_ms: 1, cost_usd: 0.5 } }, evaluation: { checks: { output: 'FAIL', handoff: 'PASS' } } });
  assert.equal(f.select('fast').status, 'WITHHELD');
  f.complete('fast-2', 'fast', { observation: { metrics: { compute_ms: 5, cost_usd: 0.2 } } });
  assert.equal(f.select('fast').status, 'SELECTED');
  const candidate = api.status(f.root).candidates.find(c => c.id === 'fast');
  assert.equal(candidate.attempted, 2);
  assert.equal(candidate.counts.FAIL, 1);
  assert.equal(candidate.metrics.compute_ms.measured_total, 6);
  assert.ok(Math.abs(candidate.metrics.cost_usd.measured_total - 0.7) < 1e-10);
  assert.equal(candidate.metrics.input_tokens.unknown_attempts, 2);
});

test('latest pending or incompletely evaluated retry invalidates a prior selection', t => {
  const f = fixture(t);
  f.propose('c1'); f.complete('base-1'); f.complete('c1-1', 'c1'); f.select('c1');
  const retry = f.request('c1-2', 'c1');
  assert.equal(api.status(f.root).selection.current_status, 'STALE');
  assert.equal(f.select('c1').status, 'WITHHELD');
  f.observe(retry);
  f.evaluate(retry, { checks: { output: 'PASS' } });
  const status = api.status(f.root);
  assert.equal(status.attempted, 3);
  assert.equal(status.candidates.find(c => c.id === 'c1').cases[0].status, 'NOT_RUN');
  assert.equal(f.select('c1').status, 'WITHHELD');
});

test('receipt identity and external evaluator attribution are checked without losing the request', t => {
  const f = fixture(t);
  const r = f.request('base-1');
  assert.throws(() => f.observe(r, { identity: { ...r.identity, source_id: 'other' } }), /identity/);
  assert.equal(api.status(f.root).attempted, 1);
  assert.equal(api.status(f.root).candidates[0].counts.NOT_RUN, 1);
  f.observe(r, { executor_id: 'validator' });
  assert.throws(() => f.evaluate(r), /own attempt/);
  assert.throws(() => f.evaluate(r, { evaluator_id: 'another' }), /authority/);
  assert.equal(f.select('base').status, 'WITHHELD');
});

test('comparison mismatch cannot qualify or create a speed ratio; unknown measurements stay null', t => {
  const f = fixture(t);
  f.propose('c1'); f.complete('base-1');
  f.complete('c1-wrong-env', 'c1', { observation: { comparison_key: 'different-hardware', metrics: { compute_ms: 0.01 } } });
  const withheld = f.select('c1');
  assert.equal(withheld.status, 'WITHHELD');
  assert.equal(withheld.comparisons[0].compute_ratio, null);
  f.complete('c1-valid', 'c1', { observation: { metrics: {} } });
  const selected = f.select('c1');
  assert.equal(selected.status, 'SELECTED');
  assert.equal(selected.comparisons[0].compute_ratio, null);
  assert.equal(api.status(f.root).candidates.find(c => c.id === 'c1').metrics.compute_ms.unknown_attempts, 1);
});

test('missing evidence stays incomplete even if a file later appears; original receipts cannot be overwritten', t => {
  const f = fixture(t);
  const r = f.request('base-1');
  const missing = path.join(f.dir, 'later.log');
  f.observe(r, { evidence: [missing] }); f.evaluate(r);
  assert.equal(f.select('base').status, 'WITHHELD');
  fs.writeFileSync(missing, 'arrived after capture');
  assert.equal(f.select('base').status, 'WITHHELD');
  assert.throws(() => f.observe(r), /already recorded/);
  assert.throws(() => f.evaluate(r), /already recorded/);
});

test('resume rejects changed source, contract, candidate or captured evidence and status shows stale selection', t => {
  const f = fixture(t);
  f.complete('base-1'); f.select('base');
  assert.equal(api.resume(f.root, f.contract).attempted, 1);
  assert.throws(() => api.resume(f.root, { ...f.contract, source_id: 'source-v2' }), /identity/);
  fs.writeFileSync(f.contract.baseline.artifact, 'edited candidate');
  assert.equal(api.status(f.root).selection.current_status, 'STALE');
  assert.throws(() => api.resume(f.root, f.contract), /changed/);
  assert.throws(() => f.request('base-2'), /changed/);
});

test('captured raw evidence changes invalidate reuse; frozen state contract changes are rejected', t => {
  const f = fixture(t);
  f.complete('base-1'); f.select('base');
  fs.writeFileSync(path.join(f.dir, 'base-1.execution.log'), 'replaced raw evidence');
  assert.equal(api.status(f.root).selection.current_status, 'STALE');
  assert.throws(() => api.resume(f.root, f.contract), /changed/);
  const filename = path.join(f.root, 'experiment.json');
  const state = JSON.parse(fs.readFileSync(filename, 'utf8'));
  state.contract.required_checks = ['output'];
  fs.writeFileSync(filename, JSON.stringify(state));
  assert.throws(() => api.status(f.root), /frozen contract/);
});

test('changing semantic evaluation requirements invalidates selection and reuse', t => {
  const f = fixture(t);
  f.complete('base-1'); f.select('base');
  fs.writeFileSync(f.contract.evaluation_spec, 'lowered requirements');
  assert.equal(api.status(f.root).selection.current_status, 'STALE');
  assert.throws(() => api.resume(f.root, f.contract), /specification changed/);
  assert.throws(() => f.request('base-2'), /specification changed/);
});

test('scope and composition lineage are enforced, without inheriting parent PASS', t => {
  const f = fixture(t);
  for (const filename of ['../src/parser.rs', '/src/parser.rs', 'src/../private', 'src//file', 'secret/file']) {
    assert.throws(() => f.propose('bad', { changes: [{ path: filename, description: 'outside' }] }), /scope|paths/);
  }
  assert.throws(() => f.propose('bad', { parents: ['unknown'] }), /unknown candidate/);
  f.propose('c1');
  assert.throws(() => f.propose('combined', { parents: ['base', 'c1'] }), /contrast/);
  f.complete('base-1'); f.complete('c1-1', 'c1');
  api.contrast(f.root, { id: 'contrast-1', attempt_ids: ['base-1', 'c1-1'], observations: ['Same fixture contracts'] });
  f.propose('combined', { parents: ['base', 'c1'], contrast_ids: ['contrast-1'] });
  assert.equal(f.select('combined').status, 'WITHHELD');
});

test('attempt budget, duplicate ids, busy writes and initialization do not overwrite valid state', t => {
  const f = fixture(t, { max_attempts: 2 });
  f.request('base-1');
  assert.throws(() => f.request('base-1'), /already exists/);
  assert.throws(() => api.init(f.root, f.contract), /already exists/);
  fs.writeFileSync(path.join(f.root, '.lock'), 'other writer');
  assert.throws(() => f.request('base-2'), /busy/);
  assert.equal(api.status(f.root).attempted, 1);
  fs.unlinkSync(path.join(f.root, '.lock'));
  f.request('base-2');
  assert.throws(() => f.request('base-3'), /budget/);
  assert.equal(api.status(f.root).attempted, 2);
});

test('unknown checks, mandatory N/A and invalid measurements never become successful coverage', t => {
  const f = fixture(t);
  const r = f.request('base-1');
  assert.throws(() => f.observe(r, { metrics: { compute_ms: -1 } }), /metric/);
  assert.throws(() => f.observe(r, { metrics: { compute_ms: NaN } }), /metric/);
  f.observe(r);
  assert.throws(() => f.evaluate(r, { checks: { invented: 'PASS' } }), /unknown check/);
  f.evaluate(r, { checks: { output: 'PASS', handoff: 'NOT_APPLICABLE' } });
  assert.equal(f.select('base').status, 'WITHHELD');
});

test('reflection requires actual evidence, retains failed lessons with limits and no promotion', t => {
  const f = fixture(t);
  const r = f.request('base-1'); f.select('base');
  const input = { cue: 'Execution failed', lesson: 'Investigate this failure', applicability: 'This fixture', limitations: 'No general claim', attempt_ids: ['base-1'] };
  assert.throws(() => api.reflect(f.root, input), /captured execution evidence/);
  f.observe(r, { status: 'FAIL' });
  const reflection = api.reflect(f.root, input);
  assert.deepEqual(reflection.statuses, ['FAIL']);
  assert.equal(reflection.promotion, 'HOST_REVIEW_REQUIRED');
  assert.equal(reflection.selection_status, 'WITHHELD');
});

test('all declared cases must be evaluated and missing cases never disappear from coverage', t => {
  const f = fixture(t, { cases: [{ id: 'normal', comparison_key: 'v1' }, { id: 'recovery', comparison_key: 'v1' }] });
  f.complete('base-normal');
  assert.equal(f.select('base').status, 'WITHHELD');
  assert.equal(api.status(f.root).candidates[0].cases[1].status, 'NOT_RUN');
  const recovery = f.request('base-recovery', 'base', 'recovery');
  f.observe(recovery); f.evaluate(recovery);
  assert.equal(f.select('base').status, 'SELECTED');
});

test('CLI errors are machine readable and help needs no experiment', () => {
  const help = spawnSync(process.execPath, [cli, '--help'], { encoding: 'utf8' });
  assert.equal(help.status, 0);
  const bad = spawnSync(process.execPath, [cli, 'invented'], { encoding: 'utf8' });
  assert.equal(bad.status, 2);
  assert.match(JSON.parse(bad.stderr).error, /arguments/);
});

test('neutral request preserves public case instructions and keeps private evaluation references local', t => {
  const cases = [{ id: 'normal', comparison_key: 'fixture-v1', task: 'Normalize records and hand off output',
    profile: { launcher: 'fixture-executor', model_used: null }, role: 'main' }];
  const f = fixture(t, { cases });
  const r = f.request('base-1');
  assert.deepEqual(r.case, cases[0]);
  assert.equal(Object.hasOwn(r, 'evaluation_spec'), false);
});
