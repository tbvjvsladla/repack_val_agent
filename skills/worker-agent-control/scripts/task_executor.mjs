#!/usr/bin/env node
// Neutral task executor bridge (prototype): request -> ONE existing external native worker CLI -> raw evidence -> public EXECUTION receipt.
//
// Responsibilities (and only these): start / status / resume of one job; frozen immutable request + prompt identity; exclusive job directory and lock;
// persisted startup/argv/cwd/PID/native session/events/stdout/stderr/exit BEFORE any interpretation; bounded capacity/budget checkpoints that resume the SAME
// native session; an execution receipt whose PASS/FAIL/NOT_RUN reports the EXECUTION only.
//
// It never judges domain correctness, never creates an evaluation PASS, never calls another skill/harness, never reads credentials, never writes a global or
// auth setting, never runs a shell string (`shell: false`, argv arrays, prompt on stdin) and inherits the caller's environment/auth/native settings and
// default permissions untouched. Node built-ins only.

import { spawn } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { performance } from 'node:perf_hooks';
import { setTimeout as delay } from 'node:timers/promises';
import { pathToFileURL } from 'node:url';

export const SCHEMA = 'task-executor-request/2';
export const CAPACITY = 'Selected model is at capacity. Please try a different model.';
export const CAPACITY_MESSAGES = [CAPACITY, CAPACITY.replace(/\.$/, '')];
export const RESUME_REMINDER =
  'Resume this same task. Inspect saved progress and any running command before acting; ' +
  'continue only unfinished work without repeating completed external effects.\n\n';

const FORBIDDEN_TOKENS = new Set([
  '--last', '--dangerously-skip-permissions', '--dangerously-bypass-approvals-and-sandbox',
  '--ignore-user-config', '--ignore-rules', '--yolo', 'bypassPermissions',
]);
const PLACEHOLDERS = new Set(['model', 'effort', 'effort_json', 'session_id', 'final_file', 'max_turns', 'workspace']);
const CODEX_START = ['exec', '--json', '-m', '{model}', '-c', 'model_reasoning_effort={effort_json}', '-o', '{final_file}', '-'];
const CODEX_RESUME = ['exec', 'resume', '--json', '-m', '{model}', '-c', 'model_reasoning_effort={effort_json}', '-o', '{final_file}', '{session_id}', '-'];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export class InputError extends Error {}

// ---------------------------------------------------------------------------------------------------------------- small helpers
const utc = () => new Date().toISOString();

export function canonical(value) {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (value && typeof value === 'object') {
    return '{' + Object.keys(value).sort().map((k) => JSON.stringify(k) + ':' + canonical(value[k])).join(',') + '}';
  }
  return JSON.stringify(value);
}

const sha256 = (data) => createHash('sha256').update(data).digest('hex');

function saveAtomic(file, value) {
  const pending = file + '.writing';
  fs.writeFileSync(pending, JSON.stringify(value, null, 2) + '\n');
  fs.renameSync(pending, file);
}

function writeNew(file, text) {
  fs.writeFileSync(file, text, { flag: 'wx', mode: 0o600 });
}

function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function isInt(value, min, max = Infinity) {
  return Number.isInteger(value) && value >= min && value <= max;
}

function nonEmpty(value) {
  return typeof value === 'string' && value.trim() !== '';
}

/** Linux process start ticks (field 22 of /proc/PID/stat), or null where unavailable: with the PID it identifies one process. */
export function startTicks(pid) {
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, 'utf8');
    return stat.slice(stat.lastIndexOf(')') + 2).split(' ')[19] ?? null;
  } catch {
    return null;
  }
}

export function processAlive(pid, ticks) {
  if (!isInt(pid, 1)) return false;
  try {
    process.kill(pid, 0);
  } catch (error) {
    if (error.code !== 'EPERM') return false;
  }
  const now = startTicks(pid);
  return ticks == null || now == null || now === ticks;
}

// ---------------------------------------------------------------------------------------------------------------- request
function tokensOf(tail) {
  const out = [];
  for (const token of tail) {
    if (typeof token !== 'string' || token === '') throw new InputError('argv templates are arrays of non-empty strings');
    out.push(token);
  }
  return out;
}

function placeholdersIn(tail) {
  const found = new Set();
  for (const token of tail) {
    for (const match of token.matchAll(/\{([^{}]*)\}/g)) {
      if (!PLACEHOLDERS.has(match[1])) throw new InputError(`unknown placeholder {${match[1]}}`);
      found.add(match[1]);
    }
  }
  return found;
}

const MODEL_FLAG = (t) => t === '--model' || t === '-m' || t.startsWith('--model=');
const EFFORT_FLAG = (t) => t === '--effort' || t.startsWith('--effort=') || t.includes('reasoning_effort');

/**
 * ORDERED comparison: removing ONLY the provider's resume verb/flag and the session placeholder from the resume template must give the start template exactly
 * (same tokens, same order, same flag/value associations, repeated flags included). Accepted resume forms: the verb `resume` + a standalone `{session_id}`
 * (codex exec resume), an adjacent pair `--resume|-r {session_id}`, or the single token `--resume={session_id}`. No sorting, no multiset equivalence.
 */
export function resumeIsStartPlusSession(start, resume) {
  const same = (a, b) => a.length === b.length && a.every((t, i) => t === b[i]);
  const at = (fn) => resume.map((t, i) => (fn(t, i) ? i : -1)).filter((i) => i >= 0);
  const without = (drop) => resume.filter((_, i) => !drop.includes(i));
  const ids = at((t) => t === '{session_id}');
  const verbs = at((t) => t === 'resume');
  const pairs = at((t, i) => (t === '--resume' || t === '-r') && resume[i + 1] === '{session_id}');
  const joined = at((t) => t === '--resume={session_id}');
  const forms = [];
  if (verbs.length === 1 && ids.length === 1 && !pairs.length && !joined.length) forms.push(without([verbs[0], ids[0]]));
  if (pairs.length === 1 && !verbs.length && ids.length === 1 && !joined.length) forms.push(without([pairs[0], pairs[0] + 1]));
  if (joined.length === 1 && !verbs.length && !ids.length && !pairs.length) forms.push(without([joined[0]]));
  return forms.length === 1 && same(forms[0], start);
}

/** Strict validation of the immutable input; returns the normalized request (templates filled in for the codex provider). */
export function validateRequest(raw, baseDir = process.cwd()) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new InputError('the request is not an object');
  const needed = ['schema', 'request_identity', 'comparison_key', 'executor_id', 'evidence_mode', 'workspace', 'prompt_file', 'provider', 'profile', 'budget'];
  const extra = Object.keys(raw).filter((k) => !needed.includes(k));
  const missing = needed.filter((k) => !(k in raw));
  if (extra.length || missing.length) throw new InputError(`request keys must be exactly: ${needed.join(', ')} (extra: ${extra}; missing: ${missing})`);
  if (raw.schema !== SCHEMA) throw new InputError(`schema must be ${SCHEMA}`);
  if (raw.request_identity === null || raw.request_identity === undefined) throw new InputError('request_identity must be present (opaque, echoed unchanged)');
  for (const key of ['comparison_key', 'executor_id']) if (!nonEmpty(raw[key])) throw new InputError(`${key} must be a non-empty string`);
  if (!['fixture', 'observed'].includes(raw.evidence_mode)) throw new InputError('evidence_mode must be fixture or observed');
  for (const key of ['workspace', 'prompt_file']) {
    if (!nonEmpty(raw[key]) || !path.isAbsolute(raw[key])) throw new InputError(`${key} must be an absolute path`);
  }
  let workspace;
  try {
    workspace = fs.realpathSync(raw.workspace);
    if (!fs.statSync(workspace).isDirectory()) throw new Error('not a directory');
  } catch {
    throw new InputError('workspace must be an existing directory');
  }
  let promptBytes;
  try {
    if (!fs.statSync(raw.prompt_file).isFile()) throw new Error('not a file');
    promptBytes = fs.readFileSync(raw.prompt_file);
  } catch {
    throw new InputError('prompt_file must be an existing regular file');
  }
  if (promptBytes.toString('utf8').trim() === '') throw new InputError('the prompt is empty');

  const provider = raw.provider;
  if (!provider || typeof provider !== 'object' || Array.isArray(provider)) throw new InputError('provider must be an object');
  const pextra = Object.keys(provider).filter((k) => !['kind', 'executable', 'start_argv', 'resume_argv'].includes(k));
  if (pextra.length) throw new InputError(`provider has unknown keys: ${pextra}`);
  if (!['codex', 'claude'].includes(provider.kind)) throw new InputError('provider.kind must be codex or claude (the two event parsers)');
  if (!nonEmpty(provider.executable)) throw new InputError('provider.executable must be a non-empty string');
  let start = provider.start_argv;
  let resume = provider.resume_argv;
  if (provider.kind === 'codex') {
    start ??= CODEX_START;
    resume ??= CODEX_RESUME;
  }
  if (!Array.isArray(start) || !Array.isArray(resume)) throw new InputError('provider.start_argv and provider.resume_argv are required for this provider');
  start = tokensOf(start);
  resume = tokensOf(resume);
  for (const token of [...start, ...resume]) {
    if (FORBIDDEN_TOKENS.has(token) || /^--permission-mode=bypass/i.test(token)) throw new InputError(`forbidden argv token: ${token}`);
  }
  const startHolders = placeholdersIn(start);
  const resumeHolders = placeholdersIn(resume);
  if (startHolders.has('session_id')) throw new InputError('a start template cannot carry {session_id}');
  if (!resumeHolders.has('session_id')) throw new InputError('a resume template must carry {session_id} (never --last)');
  // ORDERED: resume = start + only the resume verb/flag and {session_id}
  if (!resumeIsStartPlusSession(start, resume)) {
    throw new InputError('the resume template must equal the start template (same tokens, same order) plus only the resume verb/flag and {session_id}');
  }

  const profile = raw.profile;
  if (!profile || typeof profile !== 'object' || Array.isArray(profile)) throw new InputError('profile must be an object');
  const profileKeys = ['main_profile_id', 'sub_profile_id', 'cli', 'model', 'effort', 'expected_primary_models'];
  const prextra = Object.keys(profile).filter((k) => !profileKeys.includes(k));
  const prmissing = profileKeys.filter((k) => !(k in profile));
  if (prextra.length || prmissing.length) {
    throw new InputError(`profile keys must be exactly: ${profileKeys.join(', ')} (extra: ${prextra}; missing: ${prmissing}; an unspecified model/effort is an explicit null, never an omitted key)`);
  }
  for (const key of ['main_profile_id', 'sub_profile_id', 'cli']) {
    if (!nonEmpty(profile[key])) throw new InputError(`profile.${key} must be an explicit non-empty string`);
  }
  for (const key of ['model', 'effort']) {
    if (profile[key] !== null && !nonEmpty(profile[key])) throw new InputError(`profile.${key} must be a non-empty string or an explicit null (unspecified)`);
    if (provider.kind === 'codex' && profile[key] === null) throw new InputError(`profile.${key} must be an explicit string for the codex provider (the existing Codex burst contract)`);
  }
  const expected = profile.expected_primary_models;
  if (expected !== null && (!Array.isArray(expected) || expected.length === 0 || !expected.every(nonEmpty) || new Set(expected).size !== expected.length)) {
    throw new InputError('profile.expected_primary_models must be null or a non-empty array of unique exact canonical model names (no alias database)');
  }
  if (profile.main_profile_id !== profile.sub_profile_id) throw new InputError('main and sub profile ids must be the same profile id');
  // a declared selection is passed by the templates; an unspecified one is absent from them (no literal flag may contradict null)
  const passes = (names) => names.some((n) => startHolders.has(n));
  if (profile.model === null) {
    if (passes(['model'])) throw new InputError('profile.model is null: the templates must not carry {model}');
    if (start.some(MODEL_FLAG)) throw new InputError('profile.model is null: the templates must not carry a model flag');
  } else if (!passes(['model'])) {
    throw new InputError('the templates must pass the declared model ({model})');
  }
  if (profile.effort === null) {
    if (passes(['effort', 'effort_json'])) throw new InputError('profile.effort is null: the templates must not carry {effort}/{effort_json}');
    if (start.some(EFFORT_FLAG)) throw new InputError('profile.effort is null: the templates must not carry an effort option');
  } else if (!passes(['effort', 'effort_json'])) {
    throw new InputError('the templates must pass the declared effort ({effort} or {effort_json})');
  }

  const budget = raw.budget;
  if (!budget || typeof budget !== 'object' || Array.isArray(budget)) throw new InputError('budget must be an object');
  const bextra = Object.keys(budget).filter((k) => !['attempt_timeout_seconds', 'additional_capacity_attempts', 'backoff_seconds', 'burst_seconds', 'max_turns'].includes(k));
  if (bextra.length) throw new InputError(`budget has unknown keys: ${bextra}`);
  if (!isInt(budget.attempt_timeout_seconds, 1)) throw new InputError('budget.attempt_timeout_seconds must be an integer >= 1');
  if (!isInt(budget.additional_capacity_attempts, 0, 10)) throw new InputError('budget.additional_capacity_attempts must be an integer 0..10');
  if (!isInt(budget.backoff_seconds, 0)) throw new InputError('budget.backoff_seconds must be an integer >= 0');
  if (!isInt(budget.burst_seconds, 1)) throw new InputError('budget.burst_seconds must be an integer >= 1');
  if (budget.burst_seconds <= budget.attempt_timeout_seconds + budget.backoff_seconds) {
    throw new InputError('burst_seconds must fit one full attempt, the resume backoff and launch overhead');
  }
  if (startHolders.has('max_turns') && !isInt(budget.max_turns, 1)) throw new InputError('the templates carry {max_turns}: budget.max_turns must be an integer >= 1');

  return {
    request: { ...raw, provider: { kind: provider.kind, executable: provider.executable, start_argv: start, resume_argv: resume } },
    workspace_real: workspace,
    prompt_text: promptBytes.toString('utf8'),
    prompt_sha256: sha256(promptBytes),
  };
}

function fill(tail, values) {
  return tail.map((token) => token.replace(/\{([^{}]*)\}/g, (_, name) => String(values[name])));
}

// ---------------------------------------------------------------------------------------------------------------- native event interpretation
const CODEX_PRIMARY_EVENTS = new Set(['thread.started', 'turn.started', 'turn.completed']);

/** Codex `exec --json` events, the installed run_codex.py contract: exact structured capacity only. A model counts as PRIMARY only when a
 *  `thread.started`/`turn.started`/`turn.completed` event reports it in its own top-level `model` member; payload models are ignored. */
export function observeCodex(text, code, expectedSession) {
  const errors = [];
  const sessions = [];
  const primary = [];
  const active = new Set();
  let failed = false;
  let completed = false;
  const empty = (outcome, session) => ({ outcome, session, primary: [], usage_models: [], child_models: [], errors: [], format: 'codex-jsonl' });
  try {
    for (const line of text.split('\n')) {
      if (line.trim() === '') continue;
      const event = JSON.parse(line);
      if (!event || typeof event !== 'object' || Array.isArray(event)) throw new Error('event is not an object');
      if (typeof event.model === 'string' && CODEX_PRIMARY_EVENTS.has(event.type)) primary.push({ value: event.model, source: `codex.${event.type}.model` });
      switch (event.type) {
        case 'thread.started':
          if (typeof event.thread_id !== 'string' || !UUID.test(event.thread_id)) throw new Error('bad thread id');
          sessions.push(event.thread_id);
          break;
        case 'error':
          errors.push(event.message);
          break;
        case 'turn.failed':
          failed = true;
          errors.push(event.error && typeof event.error === 'object' ? event.error.message : null);
          break;
        case 'turn.completed':
          completed = true;
          break;
        case 'item.started':
        case 'item.completed': {
          const id = event.item && typeof event.item === 'object' ? event.item.id : null;
          if (typeof id !== 'string' || id === '') throw new Error('item lifecycle has no identity');
          if (event.type === 'item.started') active.add(id);
          else active.delete(id);
          break;
        }
        default:
      }
    }
  } catch {
    return empty('invalid_events', expectedSession ?? null);
  }
  const distinct = [...new Set(sessions)];
  if (distinct.length !== 1 || (expectedSession && distinct[0] !== expectedSession)) {
    return { ...empty('session_unresolved', expectedSession ?? null), errors };
  }
  let outcome;
  if (active.size) outcome = 'inflight_items';
  else if (code === 1 && failed && !completed && errors.length && errors.every((e) => CAPACITY_MESSAGES.includes(e))) outcome = 'capacity';
  else if (code === 0 && completed && !failed && errors.length === 0) outcome = 'native_success';
  else outcome = 'native_failure';
  return { outcome, session: distinct[0], primary, usage_models: [], child_models: [], errors, format: 'codex-jsonl' };
}

/** One JSON object (the `--output-format json` wrapper) or JSONL (`--output-format stream-json`); null when neither parses. */
function parseClaudeOutput(text) {
  const trimmed = text.trim();
  if (!trimmed) return null;
  try {
    const value = JSON.parse(trimmed);
    if (value && typeof value === 'object' && !Array.isArray(value)) return { format: 'wrapper', events: [value] };
  } catch { /* maybe JSONL */ }
  const events = [];
  for (const line of text.split('\n')) {
    if (line.trim() === '') continue;
    let event;
    try { event = JSON.parse(line); } catch { return null; }
    if (!event || typeof event !== 'object' || Array.isArray(event)) return null;
    events.push(event);
  }
  return events.length ? { format: 'stream-json', events } : null;
}

/**
 * Claude output. The terminal result object decides, never the exit code alone. PRIMARY model evidence exists only in stream-json: `system`/`init`
 * `model` and the top-level `message.model` of MAIN-thread `assistant` events (no `parent_tool_use_id`, not `<synthetic>`); child-thread assistant models
 * are `child_models`; `modelUsage` keys are `usage_models` (aggregate, possibly cumulative/child-inclusive) and are never promoted. Thinking metadata,
 * content blocks and tool payload models are ignored. The `--output-format json` wrapper carries no authoritative primary model: nothing is inferred.
 */
export function observeClaude(text, code, expectedSession) {
  const parsed = parseClaudeOutput(text);
  const bare = (outcome, session, extra = {}) => ({ outcome, session, primary: [], usage_models: [], child_models: [], errors: [], format: parsed?.format ?? null, ...extra });
  if (!parsed) return bare('invalid_events', expectedSession ?? null);
  const result = parsed.format === 'wrapper' ? parsed.events[0] : [...parsed.events].reverse().find((e) => e.type === 'result');
  if (!result) return bare('invalid_events', expectedSession ?? null);
  const sessions = new Set();
  let sessionBad = false;
  for (const event of parsed.events) {
    if (!('session_id' in event)) continue;
    if (typeof event.session_id === 'string' && UUID.test(event.session_id)) sessions.add(event.session_id);
    else sessionBad = true;
  }
  const primary = [];
  const child = [];
  if (parsed.format === 'stream-json') {
    for (const event of parsed.events) {
      if (event.type === 'system' && event.subtype === 'init' && typeof event.model === 'string') primary.push({ value: event.model, source: 'claude.stream.system.init.model' });
      else if (event.type === 'assistant' && event.message && typeof event.message === 'object' && typeof event.message.model === 'string' && event.message.model !== '<synthetic>') {
        if (event.parent_tool_use_id == null) primary.push({ value: event.message.model, source: 'claude.stream.assistant.message.model(main)' });
        else child.push({ value: event.message.model, source: 'claude.stream.assistant.message.model(child)' });
      }
    }
  }
  const usageModels = result.modelUsage && typeof result.modelUsage === 'object' && !Array.isArray(result.modelUsage) ? Object.keys(result.modelUsage) : [];
  const usage = {
    usage: result.usage ?? null, modelUsage: result.modelUsage ?? null, total_cost_usd: result.total_cost_usd ?? null,
    duration_api_ms: result.duration_api_ms ?? null, num_turns: result.num_turns ?? null, scope: 'unknown_possibly_cumulative_possibly_includes_children',
  };
  const base = { primary, usage_models: usageModels, child_models: child, usage, format: parsed.format };
  if (sessionBad || sessions.size !== 1 || (expectedSession && [...sessions][0] !== expectedSession)) {
    return { ...base, outcome: 'session_unresolved', session: expectedSession ?? null, errors: [] };
  }
  const session = [...sessions][0];
  const errorsKey = 'errors' in result;
  const errors = Array.isArray(result.errors) ? result.errors : [];
  const final = (outcome) => ({ ...base, outcome, session, errors });
  if (Array.isArray(result.permission_denials) && result.permission_denials.length) return final('permission_denied');
  if (result.subtype === 'error_max_turns') return final('budget_exhausted');
  if (errorsKey && !Array.isArray(result.errors)) return final('invalid_events');
  if (result.is_error !== false || (typeof result.subtype === 'string' && result.subtype.startsWith('error'))) return final('native_error');
  if (errors.length) return final('native_error');
  const hasResult = (typeof result.result === 'string' && result.result.trim() !== '') || result.structured_output != null;
  return final(code === 0 && result.subtype === 'success' && hasResult ? 'native_success' : 'native_failure');
}

/** The primary-model verdict against the caller-frozen exact expectation; never inferred from usage, never an alias lookup. */
export function judgeModel(primary, expected) {
  if (expected === null) return 'NO_EXPECTATION';
  if (!primary.length) return 'UNVERIFIED';
  return primary.every((p) => expected.includes(p.value)) ? 'MATCH' : 'MISMATCH';
}

function mergeVerdict(previous, now, expected) {
  if (previous === 'MISMATCH' || now === 'MISMATCH') return 'MISMATCH';
  if (previous === 'MATCH' || now === 'MATCH') return 'MATCH';
  return expected === null ? 'NO_EXPECTATION' : 'UNVERIFIED';
}

// ---------------------------------------------------------------------------------------------------------------- job files
const OPEN_STATES = new Set(['capacity_deferred', 'budget_exhausted']);
const RESUMABLE = OPEN_STATES;

function paths(jobDir) {
  return {
    request: path.join(jobDir, 'request.json'), prompt: path.join(jobDir, 'prompt.txt'), job: path.join(jobDir, 'job.json'),
    lock: path.join(jobDir, '.lock'), receipt: path.join(jobDir, 'receipt.json'), attempts: path.join(jobDir, 'attempts'),
  };
}

function acquireLock(file) {
  let fd;
  try {
    fd = fs.openSync(file, 'wx', 0o600);
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
    let holder = null;
    try { holder = readJson(file); } catch { /* unreadable */ }
    const alive = holder ? processAlive(holder.pid, holder.start_ticks) : null;
    return { ok: false, holder, alive };
  }
  const token = randomUUID();
  fs.writeSync(fd, JSON.stringify({ pid: process.pid, start_ticks: startTicks(process.pid), token, utc: utc() }));
  fs.closeSync(fd);
  return { ok: true, release() {
    try {
      if (readJson(file).token === token) fs.unlinkSync(file);
    } catch { /* already gone */ }
  } };
}

const round3 = (value) => Math.round(value * 1000) / 1000;

/** Public wall: the sum of the OBSERVED burst wall times (native attempts, errors, retries, observed backoff and local overhead, never counted twice). */
function wallTotal(job) {
  return job.bursts.reduce((sum, b) => sum + (typeof b.wall_ms === 'number' ? b.wall_ms : 0), 0);
}

function evidenceList(root, job) {
  const files = [paths(root).request, paths(root).prompt, paths(root).job];
  job.attempts.forEach((a, i) => {
    const dir = path.join(paths(root).attempts, 'attempt-' + String(i + 1).padStart(4, '0'));
    for (const name of ['launch.json', 'process.json', 'exit.json', 'stdout.log', 'stderr.log', 'final.txt', 'native.json']) files.push(path.join(dir, name));
  });
  return files.filter((f) => fs.existsSync(f));
}

/** The public execution receipt of a TERMINAL job (never an evaluation); null while the job is open/unknown. */
export function buildReceipt(root, job, identity) {
  let status;
  if (job.state === 'returned') status = 'PASS';
  else if (job.state === 'launch_failed') status = 'NOT_RUN';
  else if (job.state === 'canceled') status = job.attempts.length === 0 ? 'NOT_RUN' : 'FAIL';
  else if (OPEN_STATES.has(job.state) || ['working', 'ready', 'inflight', 'unknown_launch_window', 'unknown_exit'].includes(job.state)) return null;
  else status = 'FAIL';
  return receiptBody(root, job, identity, status, `execution report only, not a business verdict; state=${job.state}`);
}

function receiptBody(root, job, identity, status, note) {
  const model = job.observed.models.length ? job.observed.models.join(',') : 'UNREPORTED';
  return {
    identity,
    status,
    executor_id: job.executor_id,
    comparison_key: job.comparison_key,
    evidence: evidenceList(root, job),
    metrics: { compute_ms: null, wall_ms: round3(wallTotal(job)), cost_usd: null, input_tokens: null, output_tokens: null },
    note: `${note}; evidence_mode=${job.evidence_mode}; session=${job.native_session_id ?? 'UNREPORTED'}; observed_primary_model=${model}; model_check=${job.observed.model_check}; observed_effort=UNREPORTED; attempts=${job.attempts.length}; bursts=${job.bursts.length}; wall_ms=sum of observed burst wall (native attempts, retries, observed backoff, local overhead; gaps between bursts and unfinished bursts are not observed and not charged; unobserved_bursts=${job.bursts.filter((b) => b.wall_ms == null).length})`,
  };
}

function writeReceipt(root, job, receipt) {
  try {
    writeNew(paths(root).receipt, JSON.stringify(receipt, null, 2) + '\n');
    return { written: true, receipt };
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
    return { written: false, receipt: readJson(paths(root).receipt) };
  }
}

function persist(root, job) {
  job.updated_utc = utc();
  saveAtomic(paths(root).job, job);
}

// ---------------------------------------------------------------------------------------------------------------- one native attempt
async function runAttempt(root, job, normalized, deps, signal) {
  const { request } = normalized;
  const n = job.attempts.length + 1;
  const dir = path.join(paths(root).attempts, 'attempt-' + String(n).padStart(4, '0'));
  fs.mkdirSync(dir);
  const resuming = Boolean(job.native_session_id);
  const values = {
    model: request.profile.model, effort: request.profile.effort, effort_json: request.profile.effort === null ? null : JSON.stringify(request.profile.effort),
    session_id: job.native_session_id, final_file: path.join(dir, 'final.txt'), max_turns: request.budget.max_turns, workspace: normalized.workspace_real,
  };
  const argv = [request.provider.executable, ...fill(resuming ? request.provider.resume_argv : request.provider.start_argv, values)];
  const prompt = resuming ? RESUME_REMINDER + normalized.prompt_text : normalized.prompt_text;
  const row = { n, mode: resuming ? 'resume' : 'start', argv, cwd: normalized.workspace_real, state: 'starting', pid: null, exit_code: null, signal: null, native_active_ms: null, burst: job.bursts.length };
  job.attempts.push(row);
  job.state = 'working';
  // persisted BEFORE the child exists: a crash in this window is `unknown_launch_window`, never a blind replay
  writeNew(path.join(dir, 'launch.json'), JSON.stringify({ argv, cwd: row.cwd, started_utc: utc(), prompt_sha256: sha256(prompt), resumed_from_session: job.native_session_id }, null, 2) + '\n');
  persist(root, job);

  const outFd = fs.openSync(path.join(dir, 'stdout.log'), 'wx', 0o600);
  const errFd = fs.openSync(path.join(dir, 'stderr.log'), 'wx', 0o600);
  const began = performance.now();
  let child;
  const outcome = await new Promise((resolve) => {
    let settled = false;
    const done = (value) => { if (!settled) { settled = true; resolve(value); } };
    try {
      child = spawn(argv[0], argv.slice(1), { cwd: row.cwd, stdio: ['pipe', outFd, errFd], shell: false, detached: true, env: process.env });
    } catch (error) {
      done({ launchError: error });
      return;
    }
    child.once('error', (error) => done({ launchError: error }));
    child.once('spawn', () => {
      row.pid = child.pid;
      row.state = 'working';
      writeNew(path.join(dir, 'process.json'), JSON.stringify({ pid: child.pid, start_ticks: startTicks(child.pid), spawned_utc: utc() }, null, 2) + '\n');
      persist(root, job);
      child.stdin.on('error', () => {});
      child.stdin.end(prompt);
    });
    let interrupted = null;
    const killGroup = (sig) => { try { process.kill(-child.pid, sig); } catch { /* gone */ } };
    const stop = (why) => {
      interrupted = why;
      killGroup('SIGTERM');
      setTimeout(() => killGroup('SIGKILL'), 5000).unref();
    };
    const timer = setTimeout(() => stop('timeout'), request.budget.attempt_timeout_seconds * 1000);
    const onAbort = () => stop('canceled');
    signal?.addEventListener('abort', onAbort, { once: true });
    if (signal?.aborted) onAbort();
    child.once('close', (code, sig) => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', onAbort);
      done({ code, sig, interrupted });
    });
  });
  fs.closeSync(outFd);
  fs.closeSync(errFd);
  row.native_active_ms = round3(performance.now() - began);

  if (outcome.launchError) {
    row.state = 'launch_failed';
    row.error = outcome.launchError.code ?? 'launch_error';
    writeNew(path.join(dir, 'exit.json'), JSON.stringify({ launch_error: String(outcome.launchError.message), code: outcome.launchError.code ?? null, native_active_ms: row.native_active_ms, ended_utc: utc() }, null, 2) + '\n');
    job.state = 'launch_failed';
    persist(root, job);
    return 'launch_failed';
  }
  // the first exit is persisted before any interpretation
  row.exit_code = outcome.code;
  row.signal = outcome.sig;
  writeNew(path.join(dir, 'exit.json'), JSON.stringify({ exit_code: outcome.code, signal: outcome.sig, timed_out: outcome.interrupted === 'timeout', interrupted: outcome.interrupted, native_active_ms: row.native_active_ms, ended_utc: utc() }, null, 2) + '\n');
  row.state = 'exited';
  persist(root, job);

  const text = fs.readFileSync(path.join(dir, 'stdout.log'), 'utf8');
  const observe = request.provider.kind === 'codex' ? observeCodex : observeClaude;
  const seen = observe(text, outcome.code, job.native_session_id);
  const expectedModels = request.profile.expected_primary_models;
  const verdict = judgeModel(seen.primary, expectedModels);
  let result = seen.outcome;
  if (verdict === 'MISMATCH' && ['native_success', 'capacity', 'budget_exhausted'].includes(result)) result = 'identity_mismatch';
  if (outcome.interrupted) result = outcome.interrupted;
  else if (outcome.sig || outcome.code === null) result = 'process_signal';
  if (seen.session) job.native_session_id = seen.session;
  for (const [list, found] of [[job.observed.models, seen.primary], [job.observed.child_models, seen.child_models]]) {
    for (const m of found) if (!list.includes(m.value)) list.push(m.value);
  }
  for (const m of seen.usage_models) if (!job.observed.usage_models.includes(m)) job.observed.usage_models.push(m);
  job.observed.model_check = mergeVerdict(job.observed.model_check, verdict, expectedModels);
  writeNew(path.join(dir, 'native.json'), JSON.stringify({
    provider: request.provider.kind, source_format: seen.format, native_outcome: seen.outcome, errors: seen.errors ?? [],
    model_provenance: { requested_model: request.profile.model, expected_primary_models: expectedModels, primary_models: seen.primary, child_models: seen.child_models, usage_models: seen.usage_models, verdict, note: 'primary = authoritative main-session fields only; usage/child models are never promoted; the request is never copied into observed' },
    usage_raw: seen.usage ?? null,
  }, null, 2) + '\n');
  row.state = result;
  job.state = result === 'native_success' ? 'returned' : result;
  persist(root, job);
  return result;
}

/** A wait that an abort interrupts; the OBSERVED duration is returned (an aborted wait is not charged as the planned wait). */
async function waitCancelable(ms, deps, signal) {
  const began = performance.now();
  let aborted = Boolean(signal?.aborted);
  if (!aborted) {
    try {
      await deps.sleep(ms, signal);
    } catch (error) {
      if (error?.name === 'AbortError' || signal?.aborted) aborted = true;
      else throw error;
    }
  }
  if (signal?.aborted) aborted = true;
  return { observed_ms: round3(performance.now() - began), aborted };
}

/** One bounded burst: exact capacity failures retry the SAME native session after backoff; everything else is a state, not a retry.
 *  Cancellation (AbortSignal / SIGINT / SIGTERM) is honoured before every attempt, throughout every wait and immediately before every spawn. */
async function execute(root, job, normalized, deps, signal) {
  const burst = { n: job.bursts.length + 1, started_utc: utc(), ended_utc: null, wall_ms: null, native_active_ms: null, backoff_observed_ms: 0, overhead_ms: null, backoffs: [], canceled_phase: null };
  const began = performance.now();
  job.bursts.push(burst);
  persist(root, job);
  try {
    return await executeBurst(root, job, normalized, deps, signal, burst);
  } finally {
    burst.wall_ms = round3(performance.now() - began);
    burst.native_active_ms = round3(job.attempts.filter((a) => a.burst === burst.n).reduce((sum, a) => sum + (a.native_active_ms ?? 0), 0));
    burst.overhead_ms = round3(Math.max(0, burst.wall_ms - burst.native_active_ms - burst.backoff_observed_ms));
    burst.ended_utc = utc();
    persist(root, job);
  }
}

async function executeBurst(root, job, normalized, deps, signal, burst) {
  const budget = normalized.request.budget;
  const deadline = deps.now() + budget.burst_seconds * 1000;
  const resumeOffset = job.native_session_id ? 1 : 0;
  const before = job.state;
  let ran = false;
  const cancel = () => {
    const phase = ran ? 'between_attempts' : 'before_launch';
    job.state = 'canceled';
    job.cancel = { utc: utc(), phase: burst.backoffs.at(-1)?.aborted ? 'backoff' : phase };
    burst.canceled_phase = job.cancel.phase;
    persist(root, job);
    return 1;
  };
  for (let slot = 0; slot <= budget.additional_capacity_attempts; slot += 1) {
    const wait = budget.backoff_seconds * (slot + resumeOffset);
    if (signal?.aborted) return cancel();
    if (deadline - deps.now() < (wait + budget.attempt_timeout_seconds) * 1000) break;
    if (wait) {
      job.next_delay_seconds = wait;
      persist(root, job);
      const waited = await waitCancelable(wait * 1000, deps, signal);
      burst.backoffs.push({ planned_ms: wait * 1000, observed_ms: waited.observed_ms, aborted: waited.aborted });
      burst.backoff_observed_ms = round3(burst.backoff_observed_ms + waited.observed_ms);
      delete job.next_delay_seconds;
      if (waited.aborted) return cancel();
    }
    if (signal?.aborted) return cancel();
    ran = true;
    const result = await runAttempt(root, job, normalized, deps, signal);
    if (result === 'native_success') return 0;
    if (result === 'canceled') {
      job.cancel = { utc: utc(), phase: 'active_attempt' };
      burst.canceled_phase = 'active_attempt';
      persist(root, job);
      return 1;
    }
    if (result === 'budget_exhausted') return 75;
    if (result !== 'capacity') return 1;
    job.state = 'capacity_deferred';
    persist(root, job);
  }
  job.state = ran ? 'capacity_deferred' : before;
  persist(root, job);
  return 75;
}

// ---------------------------------------------------------------------------------------------------------------- public operations
const defaultDeps = () => ({ sleep: (ms, signal) => delay(ms, undefined, { signal }), now: () => performance.now() });

function finish(root, job, normalized, code) {
  const receipt = buildReceipt(root, job, normalized.request.request_identity);
  const written = receipt ? writeReceipt(root, job, receipt) : null;
  return { code, state: job.state, native_session_id: job.native_session_id, attempts: job.attempts.length, open: OPEN_STATES.has(job.state), receipt: written ? paths(root).receipt : null };
}

/** Start a NEW job in a NEW directory (exclusive) and run one burst in the foreground. */
export async function startJob(jobDir, requestFile, deps = defaultDeps(), signal) {
  const requestText = fs.readFileSync(requestFile, 'utf8');
  const normalized = validateRequest(JSON.parse(requestText));
  const root = path.resolve(jobDir);
  const p = paths(root);
  try {
    fs.mkdirSync(root);
  } catch (error) {
    if (error.code === 'EEXIST') return { code: 3, state: 'duplicate_job', reason: 'the job directory already exists (start never reuses or overwrites it)' };
    throw error;
  }
  fs.mkdirSync(p.attempts);
  const lock = acquireLock(p.lock);
  try {
    // the frozen inputs: the original request bytes and the prompt, never rewritten
    writeNew(p.request, requestText);
    writeNew(p.prompt, normalized.prompt_text);
    const job = {
      schema: 'task-executor-job/1', created_utc: utc(), updated_utc: utc(), state: 'ready', executor_id: normalized.request.executor_id,
      comparison_key: normalized.request.comparison_key, evidence_mode: normalized.request.evidence_mode,
      request_sha256: sha256(canonical(normalized.request)), prompt_sha256: normalized.prompt_sha256, workspace_real: normalized.workspace_real,
      requested: { model: normalized.request.profile.model, effort: normalized.request.profile.effort, expected_primary_models: normalized.request.profile.expected_primary_models, main_profile_id: normalized.request.profile.main_profile_id, sub_profile_id: normalized.request.profile.sub_profile_id, cli: normalized.request.profile.cli },
      observed: { models: [], usage_models: [], child_models: [], model_check: normalized.request.profile.expected_primary_models === null ? 'NO_EXPECTATION' : 'UNVERIFIED', effort: null }, native_session_id: null, attempts: [], bursts: [],
    };
    persist(root, job);
    const code = await execute(root, job, normalized, deps, signal);
    return finish(root, job, normalized, code);
  } finally {
    lock.release();
  }
}

function loadJob(root) {
  const p = paths(root);
  if (!fs.existsSync(p.job)) throw new InputError('not a job directory (no job.json)');
  return readJson(p.job);
}

/** The observed state of the last attempt: inflight / unknown windows are reported, never replayed. */
function liveState(root, job) {
  const last = job.attempts[job.attempts.length - 1];
  if (!last || last.state === 'exited' || !['starting', 'working'].includes(last.state)) return job.state;
  const dir = path.join(paths(root).attempts, 'attempt-' + String(last.n).padStart(4, '0'));
  if (fs.existsSync(path.join(dir, 'exit.json'))) return job.state;
  if (last.pid == null) return 'unknown_launch_window';
  let ticks = null;
  try { ticks = readJson(path.join(dir, 'process.json')).start_ticks; } catch { /* none */ }
  return processAlive(last.pid, ticks) ? 'inflight' : 'unknown_exit';
}

export function statusJob(jobDir) {
  const root = path.resolve(jobDir);
  const job = loadJob(root);
  const p = paths(root);
  let lock = null;
  if (fs.existsSync(p.lock)) {
    let holder = null;
    try { holder = readJson(p.lock); } catch { /* unreadable */ }
    lock = { present: true, pid: holder?.pid ?? null, alive: holder ? processAlive(holder.pid, holder.start_ticks) : null };
  }
  const state = lock?.alive ? 'locked_inflight' : liveState(root, job);
  return {
    state, recorded_state: job.state, open: OPEN_STATES.has(state), resumable: RESUMABLE.has(state) && Boolean(job.native_session_id) && !lock,
    native_session_id: job.native_session_id, requested: job.requested, observed: job.observed, evidence_mode: job.evidence_mode,
    attempts: job.attempts.map((a) => ({ n: a.n, mode: a.mode, state: a.state, pid: a.pid, exit_code: a.exit_code, signal: a.signal, burst: a.burst, native_active_ms: a.native_active_ms })),
    bursts: job.bursts,
    lock, receipt: fs.existsSync(p.receipt) ? p.receipt : null,
  };
}

/** Resume the SAME job from a capacity/budget checkpoint with the unchanged request; nothing is spawned unless every check passes. */
export async function resumeJob(jobDir, requestFile, deps = defaultDeps(), signal) {
  const normalized = validateRequest(readJson(requestFile));
  const root = path.resolve(jobDir);
  const p = paths(root);
  const job = loadJob(root);
  const lock = acquireLock(p.lock);
  if (!lock.ok) return { code: 3, state: lock.alive ? 'locked_inflight' : 'stale_lock', reason: 'another invocation holds or left the lock; it is never deleted automatically', lock: lock.holder };
  try {
    const changed = [];
    if (sha256(canonical(normalized.request)) !== job.request_sha256) changed.push('request');
    if (normalized.prompt_sha256 !== job.prompt_sha256) changed.push('prompt');
    if (normalized.workspace_real !== job.workspace_real) changed.push('workspace');
    if (canonical(normalized.request) !== canonical(validateRequest(readJson(p.request)).request)) changed.push('frozen_request');
    if (changed.length) return { code: 2, state: 'changed_input', changed, reason: 'resume requires the identical frozen request, prompt, workspace, profile and permission templates' };
    if (fs.existsSync(p.receipt)) return { code: 3, state: job.state, reason: 'the job has a terminal receipt; it is not overwritten or replayed', receipt: p.receipt };
    const live = liveState(root, job);
    if (['unknown_launch_window', 'inflight', 'unknown_exit'].includes(live)) {
      return { code: 4, state: live, reason: 'the last attempt has no recorded exit; nothing is spawned (an external effect may exist or still be running)' };
    }
    if (!RESUMABLE.has(job.state) || !job.native_session_id) {
      return { code: 3, state: job.state, reason: 'only a capacity/budget checkpoint with an observed native session can be resumed' };
    }
    const code = await execute(root, job, normalized, deps, signal);
    return finish(root, job, normalized, code);
  } finally {
    lock.release();
  }
}

/** Write the receipt of a terminal job, or (explicit caller decision) close an open/unknown job with NOT_RUN/FAIL; an existing receipt is never overwritten. */
export function receiptJob(jobDir, close = null) {
  const root = path.resolve(jobDir);
  const job = loadJob(root);
  const identity = validateRequest(readJson(paths(root).request)).request.request_identity;
  const live = liveState(root, job);
  if (live !== job.state) job.state = live;
  let receipt = buildReceipt(root, job, identity);
  if (!receipt) {
    if (!close) return { code: 3, state: job.state, open: true, receipt: null, reason: 'the job is open or unknown: no receipt (pass --close-open NOT_RUN|FAIL with --note to close it by explicit decision)' };
    if (!['NOT_RUN', 'FAIL'].includes(close.status) || !nonEmpty(close.note)) throw new InputError('--close-open takes NOT_RUN or FAIL and a non-empty --note');
    receipt = receiptBody(root, job, identity, close.status, `closed by explicit caller decision: ${close.note}; state=${job.state}`);
  }
  const written = writeReceipt(root, job, receipt);
  return { code: 0, state: job.state, receipt: paths(root).receipt, written: written.written, status: written.receipt.status };
}

// ---------------------------------------------------------------------------------------------------------------- CLI
const USAGE = `usage:
  task_executor.mjs run <new-job-dir> --request FILE
  task_executor.mjs status <job-dir>
  task_executor.mjs resume <job-dir> --request FILE
  task_executor.mjs receipt <job-dir> [--close-open NOT_RUN|FAIL --note TEXT]
exit: 0 native returned | 75 open capacity/budget checkpoint | 1 terminal failure | 2 invalid input / changed input | 3 refused (duplicate, lock, terminal) | 4 unknown/inflight (no replay)`;

function option(args, name) {
  const at = args.indexOf(name);
  return at >= 0 ? args[at + 1] : undefined;
}

export async function main(argv) {
  const [command, jobDir, ...rest] = argv;
  const out = (value) => process.stdout.write(JSON.stringify(value) + '\n');
  try {
    if (!command || !jobDir) throw new InputError(USAGE);
    const controller = new AbortController();
    for (const sig of ['SIGINT', 'SIGTERM']) process.once(sig, () => controller.abort());
    let result;
    if (command === 'run' || command === 'resume') {
      const request = option(rest, '--request');
      if (!request) throw new InputError('--request FILE is required');
      result = command === 'run' ? await startJob(jobDir, request, undefined, controller.signal) : await resumeJob(jobDir, request, undefined, controller.signal);
    } else if (command === 'status') {
      result = { code: 0, ...statusJob(jobDir) };
    } else if (command === 'receipt') {
      const status = option(rest, '--close-open');
      result = receiptJob(jobDir, status ? { status, note: option(rest, '--note') } : null);
    } else {
      throw new InputError(USAGE);
    }
    const { code, ...body } = result;
    out(body);
    return code;
  } catch (error) {
    process.stderr.write(JSON.stringify({ error: error.message, input_error: error instanceof InputError }) + '\n');
    return 2;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main(process.argv.slice(2));
}
