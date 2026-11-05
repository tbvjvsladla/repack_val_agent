#!/usr/bin/env python3
"""One bounded Codex execution burst; exact capacity failures retain the native session."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

CAPACITY = 'Selected model is at capacity. Please try a different model.'
CAPACITY_MESSAGES = (CAPACITY, CAPACITY.removesuffix('.'))


def save(path, value):
    pending = path.with_suffix('.writing')
    pending.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    pending.replace(path)


def request(path):
    raw = json.loads(path.read_text())
    needed = {'workspace', 'executable', 'model', 'effort', 'prompt_file', 'authorization_file',
              'attempt_timeout_seconds', 'additional_capacity_attempts', 'backoff_seconds', 'burst_seconds'}
    if set(raw) != needed:
        raise ValueError('request keys must be: ' + ', '.join(sorted(needed)))
    for key in ['model', 'effort', 'executable']:
        if not isinstance(raw[key], str) or not raw[key].strip():
            raise ValueError(key + ' must be a nonempty string')
    for key, minimum in [('attempt_timeout_seconds', 1), ('additional_capacity_attempts', 0),
                         ('backoff_seconds', 0), ('burst_seconds', 1)]:
        if type(raw[key]) is not int or raw[key] < minimum:
            raise ValueError(key + ' has invalid bounds')
    if raw['additional_capacity_attempts'] > 10:
        raise ValueError('one burst supports at most 10 additional capacity attempts')
    if raw['burst_seconds'] <= raw['attempt_timeout_seconds'] + raw['backoff_seconds']:
        raise ValueError('burst must fit one full attempt, resume backoff and launch overhead')
    workspace = Path(raw['workspace']).resolve(strict=True)
    if not workspace.is_dir():
        raise ValueError('workspace is not a directory')
    raw['workspace'] = str(workspace)
    authorization = json.loads(Path(raw['authorization_file']).read_text())
    if (set(authorization) != {'authorization', 'source'} or
            any(not isinstance(v, str) or not v.strip() for v in authorization.values())):
        raise ValueError('authorization requires verbatim user text and source')
    return dict(raw, prompt_text=Path(raw['prompt_file']).read_text(), authorization=authorization)


def observe(path, code, expected_session):
    errors, sessions = [], []
    active_items = set()
    failed = completed = False
    try:
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if not isinstance(event, dict):
                raise ValueError('event is not an object')
            kind = event.get('type')
            if kind == 'thread.started':
                sid = event.get('thread_id')
                uuid.UUID(sid)
                sessions.append(sid)
            elif kind == 'error':
                errors.append(event.get('message'))
            elif kind == 'turn.failed':
                failed = True
                error = event.get('error')
                errors.append(error.get('message') if isinstance(error, dict) else None)
            elif kind == 'turn.completed':
                completed = True
            elif kind in {'item.started', 'item.completed'}:
                item = event.get('item')
                item_id = item.get('id') if isinstance(item, dict) else None
                if not isinstance(item_id, str) or not item_id:
                    raise ValueError('item lifecycle has no identity')
                if kind == 'item.started':
                    active_items.add(item_id)
                else:
                    active_items.discard(item_id)
    except (OSError, ValueError, TypeError, AttributeError):
        return 'invalid_events', expected_session
    if not sessions or len(set(sessions)) != 1 or (expected_session and sessions[0] != expected_session):
        return 'session_unresolved', expected_session
    sid = sessions[0]
    if active_items:
        return 'inflight_items', sid
    if code == 1 and failed and not completed and errors and all(e in CAPACITY_MESSAGES for e in errors):
        return 'capacity', sid
    if code == 0 and completed and not failed and not errors:
        return 'native_success', sid
    return 'native_failure', sid


def execute(config, root, meta):
    deadline = time.monotonic() + config['burst_seconds']
    burst_id = uuid.uuid4().hex
    # A later burst's first resume is still a retry of the previous capacity failure.
    resume_offset = int(bool(meta.get('native_session_id')))
    for slot in range(config['additional_capacity_attempts'] + 1):
        delay = config['backoff_seconds'] * (slot + resume_offset)
        if deadline - time.monotonic() < delay + config['attempt_timeout_seconds']:
            break
        if delay:
            meta.update(state='backoff', next_delay_seconds=delay)
            save(root / 'receipt.json', meta)
            time.sleep(delay)
        sid = meta.get('native_session_id')
        argv = [config['executable'], 'exec'] + (['resume'] if sid else [])
        argv += ['--json', '-m', config['model'], '-c', 'model_reasoning_effort=' + json.dumps(config['effort'])]
        attempt = root / ('attempt-%04d' % (len(meta['attempts']) + 1))
        attempt.mkdir()
        argv += ['-o', str(attempt.resolve() / 'final.txt')]
        argv += ([sid] if sid else []) + ['-']
        prompt = config['prompt_text']
        if sid:
            prompt = ('Resume this same task. Inspect saved progress and any running command before acting; '
                      'continue only unfinished work without repeating completed external effects.\n\n' + prompt)
        row = {'burst_id': burst_id, 'argv': argv, 'state': 'starting', 'pid': None, 'exit_code': None}
        meta['attempts'].append(row)
        meta.update(state='working')
        save(root / 'receipt.json', meta)
        started = time.monotonic()
        with (attempt / 'events.jsonl').open('w') as so, (attempt / 'stderr.log').open('w') as se:
            try:
                proc = subprocess.Popen(argv, cwd=config['workspace'], stdin=subprocess.PIPE,
                                        stdout=so, stderr=se, text=True, start_new_session=True)
            except OSError as error:
                row.update(state='launch_failed', error=str(error))
                meta['state'] = 'failed'
                save(root / 'receipt.json', meta)
                return 1
            row.update(pid=proc.pid, state='working')
            save(root / 'receipt.json', meta)
            interrupted = None
            try:
                proc.communicate(prompt, timeout=config['attempt_timeout_seconds'])
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                interrupted = 'timeout' if isinstance(error, subprocess.TimeoutExpired) else 'canceled'
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()
        outcome, observed_session = observe(attempt / 'events.jsonl', proc.returncode, sid)
        if interrupted or proc.returncode < 0:
            outcome = interrupted or 'process_signal'
        row.update(state=outcome, exit_code=proc.returncode,
                   wall_seconds=round(time.monotonic() - started, 6))
        meta['native_session_id'] = observed_session
        if outcome == 'native_success':
            meta['state'] = 'returned'  # Native return is not externally accepted task completion.
            save(root / 'receipt.json', meta)
            return 0
        if outcome != 'capacity':
            meta['state'] = outcome
            save(root / 'receipt.json', meta)
            return 1
        meta['state'] = 'capacity_deferred'
        save(root / 'receipt.json', meta)
    meta['state'] = 'capacity_deferred'
    save(root / 'receipt.json', meta)
    return 75


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    where = parser.add_mutually_exclusive_group(required=True)
    where.add_argument('--output-dir', type=Path)
    where.add_argument('--resume-run', type=Path)
    args = parser.parse_args()
    try:
        config = request(args.request)
        root = (args.resume_run or args.output_dir).resolve()
        if not args.resume_run:
            root.mkdir(parents=True, exist_ok=False)
        lock = root / '.lock'
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        try:
            if args.resume_run:
                meta = json.loads((root / 'receipt.json').read_text())
                if (meta['request'] != config or meta['state'] != 'capacity_deferred' or
                        not meta.get('native_session_id')):
                    raise ValueError('resume requires the unchanged request and a capacity-deferred native session')
            else:
                meta = {'request': config, 'state': 'ready', 'native_session_id': None, 'attempts': []}
            try:
                code = execute(config, root, meta)
            except KeyboardInterrupt:
                meta['state'] = 'canceled'
                save(root / 'receipt.json', meta)
                code = 130
        finally:
            lock.unlink()
        print(json.dumps({'state': meta['state'], 'native_session_id': meta['native_session_id'],
                          'attempt_count': len(meta['attempts']), 'receipt': str(root / 'receipt.json')}))
        return code
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({'error': str(error)}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
