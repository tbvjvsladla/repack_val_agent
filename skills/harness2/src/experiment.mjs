import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';

const STATUSES = new Set(['PASS', 'FAIL', 'NOT_RUN', 'NOT_APPLICABLE', 'STALE']);
const METRICS = ['compute_ms', 'wall_ms', 'cost_usd', 'input_tokens', 'output_tokens'];
const ensure = (ok, message) => { if (!ok) throw new Error(message); };
const nonempty = value => typeof value === 'string' && value.trim().length > 0;
const canonical = value => JSON.stringify(value, (_, v) =>
  v && typeof v === 'object' && !Array.isArray(v)
    ? Object.fromEntries(Object.keys(v).sort().map(k => [k, v[k]])) : v);
const digest = value => createHash('sha256').update(value).digest('hex');
const id = value => ensure(typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(value), 'invalid id');
const strings = value => Array.isArray(value) && value.length > 0 && value.every(nonempty);
const unique = values => new Set(values).size === values.length;
const plainObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function scopedPath(value) {
  ensure(nonempty(value) && !path.posix.isAbsolute(value) && !value.includes('\\'), 'scope paths must be relative POSIX paths');
  const parts = value.split('/');
  ensure(parts.every(p => p && p !== '.' && p !== '..'), 'scope paths must not contain empty, dot or parent segments');
  return value;
}

function validateContract(c) {
  ensure(plainObject(c), 'contract must be an object');
  for (const key of ['task_id', 'experiment_id', 'evaluation_id', 'source_id', 'owner_id', 'evaluator_id', 'goal']) {
    ensure(nonempty(c[key]), `contract requires ${key}`);
  }
  ensure(['fixture', 'observed'].includes(c.evidence_mode), 'evidence_mode must be fixture or observed');
  ensure(strings(c.scope) && unique(c.scope), 'scope must contain unique paths');
  c.scope.forEach(scopedPath);
  ensure(strings(c.required_checks) && unique(c.required_checks), 'required_checks must be nonempty and unique');
  c.required_checks.forEach(id);
  ensure(Array.isArray(c.cases) && c.cases.length > 0, 'cases required');
  c.cases.forEach(item => { id(item.id); ensure(nonempty(item.comparison_key), 'case comparison_key required'); });
  ensure(unique(c.cases.map(item => item.id)), 'duplicate case');
  ensure(Number.isSafeInteger(c.max_attempts) && c.max_attempts > 0, 'positive max_attempts required');
  ensure(plainObject(c.baseline), 'baseline required');
  id(c.baseline.id);
  ensure(nonempty(c.baseline.artifact), 'baseline artifact required');
  ensure(nonempty(c.evaluation_spec), 'evaluation_spec file required');
}

function reference(filename) {
  ensure(nonempty(filename), 'evidence path must be a string');
  const absolute = path.resolve(filename);
  try {
    ensure(fs.statSync(absolute).isFile(), `not a file: ${absolute}`);
    return { path: absolute, sha256: digest(fs.readFileSync(absolute)), available: true };
  } catch (error) {
    if (error.code === 'ENOENT' || error.code === 'ENOTDIR') return { path: absolute, sha256: null, available: false };
    throw error;
  }
}

function references(value) {
  ensure(Array.isArray(value), 'evidence must be an array of file paths');
  return value.map(reference);
}

function intact(ref) {
  if (!ref.available) return false;
  try { return reference(ref.path).sha256 === ref.sha256; } catch { return false; }
}

const evidenceValid = refs => refs.length > 0 && refs.every(intact);
const statePath = root => path.join(root, 'experiment.json');

function load(root) {
  const s = JSON.parse(fs.readFileSync(statePath(root), 'utf8'));
  ensure(s.version === 1, 'unsupported state version');
  validateContract(s.contract);
  ensure(s.contract_identity === digest(canonical(s.contract)), 'frozen contract changed');
  return s;
}

function save(root, s) {
  const temporary = path.join(root, `.experiment.${process.pid}.tmp`);
  try {
    fs.writeFileSync(temporary, JSON.stringify(s, null, 2) + '\n', { flag: 'wx' });
    fs.renameSync(temporary, statePath(root));
  } finally {
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}

function locked(root, callback) {
  fs.mkdirSync(root, { recursive: true });
  const filename = path.join(root, '.lock');
  let lock;
  try { lock = fs.openSync(filename, 'wx'); }
  catch (error) {
    if (error.code === 'EEXIST') throw new Error('experiment busy: retry after current command; inspect .lock if its process stopped');
    throw error;
  }
  try { return callback(); }
  finally { fs.closeSync(lock); fs.unlinkSync(filename); }
}

function integrityIssues(s) {
  const issues = [];
  if (!intact(s.evaluation_spec)) issues.push('captured evaluation specification changed/missing');
  for (const candidate of s.candidates) {
    if (!intact(candidate.artifact)) issues.push(`candidate artifact changed/missing: ${candidate.id}`);
  }
  for (const attempt of s.attempts) {
    for (const receipt of [attempt.observation, attempt.evaluation].filter(Boolean)) {
      for (const ref of receipt.evidence) {
        if (ref.available && !intact(ref)) issues.push(`captured evidence changed/missing: ${attempt.request.identity.attempt_id}: ${ref.path}`);
      }
    }
  }
  return issues;
}

function mutate(root, callback) {
  return locked(root, () => {
    const s = load(root);
    ensure(integrityIssues(s).length === 0, `captured inputs changed; start a new experiment: ${integrityIssues(s).join('; ')}`);
    const result = callback(s);
    save(root, s);
    return result;
  });
}

const candidateById = (s, value) => {
  const candidate = s.candidates.find(c => c.id === value);
  ensure(candidate, `unknown candidate: ${value}`);
  return candidate;
};
const attemptById = (s, value) => {
  const attempt = s.attempts.find(a => a.request.identity.attempt_id === value);
  ensure(attempt, `unknown attempt: ${value}`);
  return attempt;
};

function matchIdentity(attempt, receipt) {
  ensure(canonical(attempt.request.identity) === canonical(receipt.identity), 'receipt identity does not match frozen request');
}

function latest(s, candidateId, caseId) {
  return s.attempts.filter(a => a.request.identity.candidate_id === candidateId && a.request.identity.case_id === caseId).at(-1);
}

function effectiveStatus(s, attempt) {
  if (!attempt) return 'NOT_RUN';
  const o = attempt.observation;
  const e = attempt.evaluation;
  if (o?.status === 'FAIL' || s.contract.required_checks.some(key => e?.checks[key] === 'FAIL')) return 'FAIL';
  if (o?.status === 'STALE' || s.contract.required_checks.some(key => e?.checks[key] === 'STALE')) return 'STALE';
  if (integrityIssuesForAttempt(s, attempt)) return 'STALE';
  if (o?.status !== 'PASS' || !e || !evidenceValid(o.evidence) || !evidenceValid(e.evidence)) return 'NOT_RUN';
  if (o.comparison_key !== attempt.request.identity.comparison_key) return 'NOT_RUN';
  return s.contract.required_checks.every(key => e.checks[key] === 'PASS') ? 'PASS' : 'NOT_RUN';
}

function integrityIssuesForAttempt(s, a) {
  const candidate = candidateById(s, a.request.identity.candidate_id);
  return !intact(s.evaluation_spec) || !intact(candidate.artifact) || [a.observation, a.evaluation].filter(Boolean)
    .some(receipt => receipt.evidence.some(ref => ref.available && !intact(ref)));
}

function candidateSummary(s, candidate) {
  const attempts = s.attempts.filter(a => a.request.identity.candidate_id === candidate.id);
  const counts = Object.fromEntries(['PASS', 'FAIL', 'NOT_RUN', 'STALE'].map(status => [status, 0]));
  attempts.forEach(a => { counts[effectiveStatus(s, a)]++; });
  const cases = s.contract.cases.map(item => {
    const a = latest(s, candidate.id, item.id);
    return { case_id: item.id, latest_attempt: a?.request.identity.attempt_id ?? null, status: effectiveStatus(s, a) };
  });
  const metrics = Object.fromEntries(METRICS.map(key => {
    const values = attempts.map(a => a.observation?.metrics[key]);
    const known = values.filter(value => typeof value === 'number');
    return [key, { measured_total: known.length ? known.reduce((a, b) => a + b, 0) : null, measured_attempts: known.length, unknown_attempts: values.length - known.length }];
  }));
  return { id: candidate.id, qualified: intact(candidate.artifact) && cases.every(item => item.status === 'PASS'), cases, attempted: attempts.length, counts, metrics };
}

function summarize(s) {
  const candidates = s.candidates.map(candidate => candidateSummary(s, candidate));
  const selection = s.selections.at(-1) ?? null;
  let selectionState = selection?.status ?? 'NOT_RUN';
  if (selection?.status === 'SELECTED') {
    const relevant = [s.contract.baseline.id, selection.candidate_id];
    const current = candidates.filter(c => relevant.includes(c.id)).flatMap(c => c.cases.map(item => item.latest_attempt));
    if (!relevant.every(value => candidates.find(c => c.id === value)?.qualified)
      || canonical([...new Set(current)].sort()) !== canonical([...selection.attempt_ids].sort())) selectionState = 'STALE';
  }
  return {
    task_id: s.contract.task_id, experiment_id: s.contract.experiment_id,
    evidence_mode: s.contract.evidence_mode, attempted: s.attempts.length,
    max_attempts: s.contract.max_attempts, candidates, integrity_issues: integrityIssues(s),
    selection: selection ? { ...selection, current_status: selectionState } : null,
    reflections: s.reflections.length,
    final_acceptance: 'HOST_REQUIRED',
    performance_claim_allowed: false,
    performance_note: 'Paired observations are descriptive; fixtures, identity labels and a local selection do not establish a target performance gain.'
  };
}

export function init(root, contract) {
  validateContract(contract);
  return locked(root, () => {
    ensure(!fs.existsSync(statePath(root)), 'experiment already exists; use resume with the original contract');
    const artifact = reference(contract.baseline.artifact);
    ensure(artifact.available, 'baseline artifact missing');
    const evaluationSpec = reference(contract.evaluation_spec);
    ensure(evaluationSpec.available, 'evaluation specification missing');
    const s = { version: 1, contract, contract_identity: digest(canonical(contract)), evaluation_spec: evaluationSpec,
      candidates: [{ id: contract.baseline.id, kind: 'baseline', parents: [], artifact, changes: [], author_id: contract.owner_id }],
      attempts: [], contrasts: [], selections: [], reflections: [] };
    save(root, s);
    return summarize(s);
  });
}

export function propose(root, input) {
  return mutate(root, s => {
    id(input.id);
    ensure(!s.candidates.some(c => c.id === input.id), 'candidate id already exists');
    ensure(strings(input.parents) && unique(input.parents), 'candidate parents required and unique');
    input.parents.forEach(value => candidateById(s, value));
    ensure(nonempty(input.hypothesis) && nonempty(input.author_id), 'hypothesis and author_id required');
    ensure(Array.isArray(input.changes) && input.changes.length > 0, 'declared changes required');
    input.changes.forEach(change => {
      scopedPath(change.path);
      ensure(s.contract.scope.some(prefix => change.path === prefix || change.path.startsWith(prefix + '/')), 'declared change outside approved scope');
      ensure(nonempty(change.description), 'change description required');
    });
    const artifact = reference(input.artifact);
    ensure(artifact.available, 'candidate artifact missing');
    const contrastIds = input.contrast_ids ?? [];
    ensure(Array.isArray(contrastIds) && unique(contrastIds), 'contrast_ids must be unique');
    const contrasts = contrastIds.map(value => {
      const contrast = s.contrasts.find(c => c.id === value);
      ensure(contrast, `unknown contrast: ${value}`);
      return contrast;
    });
    if (input.parents.length > 1) {
      const compared = contrasts.flatMap(c => c.attempt_ids.map(value => attemptById(s, value).request.identity.candidate_id));
      ensure(input.parents.every(value => compared.includes(value)), 'composition requires contrast evidence for every parent');
    }
    const candidate = { id: input.id, kind: input.parents.length > 1 ? 'composition' : 'proposal', parents: input.parents,
      artifact, changes: input.changes, hypothesis: input.hypothesis, author_id: input.author_id, contrast_ids: contrastIds };
    s.candidates.push(candidate);
    return candidate;
  });
}

export function request(root, input) {
  return mutate(root, s => {
    id(input.attempt_id);
    ensure(!s.attempts.some(a => a.request.identity.attempt_id === input.attempt_id), 'attempt id already exists; do not execute it twice');
    ensure(s.attempts.length < s.contract.max_attempts, 'attempt budget exhausted');
    const candidate = candidateById(s, input.candidate_id);
    const caseSpec = s.contract.cases.find(c => c.id === input.case_id);
    ensure(caseSpec, 'unknown case');
    const identity = Object.fromEntries(['task_id', 'experiment_id', 'evaluation_id', 'source_id'].map(key => [key, s.contract[key]]));
    Object.assign(identity, { candidate_id: candidate.id, candidate_fingerprint: candidate.artifact.sha256,
      case_id: caseSpec.id, attempt_id: input.attempt_id, comparison_key: caseSpec.comparison_key });
    const result = { identity, goal: s.contract.goal, scope: s.contract.scope, required_checks: s.contract.required_checks,
      case: caseSpec, artifact: candidate.artifact, evidence_mode: s.contract.evidence_mode };
    s.attempts.push({ request: result, observation: null, evaluation: null });
    return result;
  });
}

export function observe(root, input) {
  return mutate(root, s => {
    const attempt = attemptById(s, input.identity?.attempt_id);
    matchIdentity(attempt, input);
    ensure(!attempt.observation, 'observation already recorded; use a new attempt for retry');
    ensure(STATUSES.has(input.status), 'invalid observation status');
    ensure(nonempty(input.executor_id) && nonempty(input.comparison_key), 'executor_id and actual comparison_key required');
    ensure(plainObject(input.metrics), 'metrics must be an object (unknown values may be null)');
    for (const [key, value] of Object.entries(input.metrics)) {
      ensure(METRICS.includes(key) && (value === null || (typeof value === 'number' && Number.isFinite(value) && value >= 0)), 'invalid metric');
    }
    const observation = { status: input.status, executor_id: input.executor_id, comparison_key: input.comparison_key,
      metrics: input.metrics, evidence: references(input.evidence), note: input.note ?? '' };
    attempt.observation = observation;
    return { attempt_id: input.identity.attempt_id, observation, effective_status: effectiveStatus(s, attempt) };
  });
}

export function evaluate(root, input) {
  return mutate(root, s => {
    const attempt = attemptById(s, input.identity?.attempt_id);
    matchIdentity(attempt, input);
    ensure(attempt.observation, 'record execution observation before evaluation');
    ensure(!attempt.evaluation, 'evaluation already recorded; do not overwrite prior evidence');
    ensure(input.evaluator_id === s.contract.evaluator_id, 'evaluator does not match frozen authority');
    ensure(input.evaluator_id !== attempt.observation.executor_id, 'executor cannot evaluate its own attempt');
    ensure(plainObject(input.checks), 'checks must be an object');
    for (const [key, value] of Object.entries(input.checks)) {
      ensure(s.contract.required_checks.includes(key) && STATUSES.has(value), 'unknown check or invalid check status');
    }
    attempt.evaluation = { evaluator_id: input.evaluator_id,
      checks: Object.fromEntries(s.contract.required_checks.map(key => [key, input.checks[key] ?? 'NOT_RUN'])),
      evidence: references(input.evidence), note: input.note ?? '' };
    return { attempt_id: input.identity.attempt_id, evaluation: attempt.evaluation, effective_status: effectiveStatus(s, attempt) };
  });
}

export function contrast(root, input) {
  return mutate(root, s => {
    id(input.id);
    ensure(!s.contrasts.some(c => c.id === input.id), 'contrast id already exists');
    ensure(strings(input.attempt_ids) && unique(input.attempt_ids) && input.attempt_ids.length >= 2, 'contrast requires two distinct attempts');
    input.attempt_ids.forEach(value => attemptById(s, value));
    ensure(strings(input.observations), 'contrast observations required');
    const entry = { id: input.id, attempt_ids: input.attempt_ids, observations: input.observations,
      statuses: input.attempt_ids.map(value => ({ attempt_id: value, status: effectiveStatus(s, attemptById(s, value)) })),
      claim: 'Observations are hypotheses, not correctness or causal proof.' };
    s.contrasts.push(entry);
    return entry;
  });
}

export function select(root, input) {
  return mutate(root, s => {
    ensure(input.owner_id === s.contract.owner_id && nonempty(input.reason), 'host owner_id and reason required');
    candidateById(s, input.candidate_id);
    const baseline = candidateSummary(s, candidateById(s, s.contract.baseline.id));
    const candidate = candidateSummary(s, candidateById(s, input.candidate_id));
    const qualified = baseline.qualified && candidate.qualified;
    const attemptIds = [...new Set([...baseline.cases, ...candidate.cases].map(c => c.latest_attempt).filter(Boolean))];
    const comparisons = s.contract.cases.map(c => {
      const base = latest(s, baseline.id, c.id);
      const proposed = latest(s, candidate.id, c.id);
      const b = base?.observation?.metrics.compute_ms;
      const p = proposed?.observation?.metrics.compute_ms;
      const comparable = qualified && typeof b === 'number' && typeof p === 'number' && b > 0 && p > 0;
      return { case_id: c.id, comparable, baseline_compute_ms: b ?? null, candidate_compute_ms: p ?? null,
        compute_ratio: comparable ? b / p : null, evidence_mode: s.contract.evidence_mode };
    });
    const selection = { status: qualified ? 'SELECTED' : 'WITHHELD', candidate_id: input.candidate_id,
      reason: input.reason, owner_id: input.owner_id, attempt_ids: attemptIds, comparisons,
      final_acceptance: 'HOST_REQUIRED' };
    s.selections.push(selection);
    return selection;
  });
}

export function reflect(root, input) {
  return mutate(root, s => {
    ensure(s.selections.length > 0, 'record a selection decision, including WITHHELD, before reflection');
    for (const key of ['cue', 'lesson', 'applicability', 'limitations']) ensure(nonempty(input[key]), `reflection requires ${key}`);
    ensure(strings(input.attempt_ids) && unique(input.attempt_ids), 'reflection needs attempt evidence');
    input.attempt_ids.forEach(value => {
      const attempt = attemptById(s, value);
      ensure(attempt.observation && evidenceValid(attempt.observation.evidence), 'reflection requires captured execution evidence, including for failures');
    });
    const entry = { cue: input.cue, lesson: input.lesson, applicability: input.applicability, limitations: input.limitations,
      attempt_ids: input.attempt_ids, statuses: input.attempt_ids.map(value => effectiveStatus(s, attemptById(s, value))),
      selection_index: s.selections.length - 1, selection_status: summarize(s).selection.current_status,
      evidence_mode: s.contract.evidence_mode, promotion: 'HOST_REVIEW_REQUIRED' };
    s.reflections.push(entry);
    return entry;
  });
}

export const status = root => summarize(load(root));

export function resume(root, contract) {
  const s = load(root);
  ensure(canonical(contract) === canonical(s.contract), 'resume contract identity mismatch');
  const issues = integrityIssues(s);
  ensure(issues.length === 0, `captured inputs changed; cannot reuse: ${issues.join('; ')}`);
  return summarize(s);
}
