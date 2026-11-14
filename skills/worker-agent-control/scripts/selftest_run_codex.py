"""Actual subprocess fixtures for capacity continuation. Never calls a real model."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

RUNNER = Path(__file__).with_name('run_codex.py')
SESSION = '00000000-0000-4000-8000-000000001234'
FAKE = r'''
import json,pathlib,sys,time
p=pathlib.Path(__file__).parent
mode=(p/'mode').read_text()
history=p/'history.json'
rows=json.loads(history.read_text()) if history.exists() else []
rows.append(sys.argv[1:]);history.write_text(json.dumps(rows))
sys.stdin.read()
if mode=='raw':
 print('Selected model is at capacity. Please try a different model.',file=sys.stderr);sys.exit(1)
if mode=='badjson':print('{invalid');sys.exit(1)
sid='00000000-0000-4000-8000-000000001234'
if mode=='mismatch' and len(rows)>1:sid='00000000-0000-4000-8000-000000009999'
print(json.dumps({'type':'thread.started','thread_id':sid}),flush=True)
if mode=='timeout':time.sleep(10)
if mode=='unresolved' and len(rows)==1:
 print(json.dumps({'type':'item.started','item':{'id':'command-1','type':'command_execution','status':'in_progress'}}))
if mode in ['forever','mismatch'] or (mode=='twice' and len(rows)<=2) or (mode in ['unresolved','no_dot'] and len(rows)==1):
 msg='Selected model is at capacity. Please try a different model.'
 if mode=='no_dot':msg=msg.removesuffix('.')
elif mode=='permission':msg='Permission denied'
else:
 print(json.dumps({'type':'turn.completed'}));sys.exit(0)
print(json.dumps({'type':'error','message':msg}))
print(json.dumps({'type':'turn.failed','error':{'message':msg}}));sys.exit(1)
'''


class CapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='capacity-fixture.')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        exe = self.root / 'codex'
        exe.write_text('#!' + sys.executable + '\n' + FAKE)
        exe.chmod(0o755)
        (self.root / 'prompt.txt').write_text('Read the saved fixture checkpoint; no external actions.')
        (self.root / 'auth.json').write_text(json.dumps({'authorization': 'fixture retry permitted', 'source': 'selftest'}))
        self.config = {'workspace': str(self.root), 'executable': str(exe), 'model': 'fixture-model',
                       'effort': 'medium', 'prompt_file': str(self.root / 'prompt.txt'),
                       'authorization_file': str(self.root / 'auth.json'), 'attempt_timeout_seconds': 2,
                       'additional_capacity_attempts': 2, 'backoff_seconds': 0, 'burst_seconds': 30}
        self.config_path = self.root / 'request.json'
        self.out = self.root / 'run'

    def run_fixture(self, mode, resume=False):
        self.config_path.write_text(json.dumps(self.config))
        (self.root / 'mode').write_text(mode)
        return subprocess.run([sys.executable, str(RUNNER), '--request', str(self.config_path),
                               '--resume-run' if resume else '--output-dir', str(self.out)],
                              capture_output=True, text=True, timeout=15)

    def receipt(self):
        return json.loads((self.out / 'receipt.json').read_text())

    def test_two_capacity_errors_resume_same_session_then_return(self):
        result = self.run_fixture('twice')
        self.assertEqual(result.returncode, 0, result.stdout)
        receipt = self.receipt()
        self.assertEqual([a['state'] for a in receipt['attempts']], ['capacity', 'capacity', 'native_success'])
        self.assertEqual(receipt['native_session_id'], SESSION)
        for row in receipt['attempts'][1:]:
            self.assertEqual(row['argv'][1:3], ['exec', 'resume'])
            self.assertIn(SESSION, row['argv'])
            self.assertIn('fixture-model', row['argv'])
            self.assertNotIn('--last', row['argv'])
        self.assertFalse((self.out / '.lock').exists())

    def test_exhausted_burst_keeps_history_and_continues(self):
        self.config['additional_capacity_attempts'] = 0
        self.assertEqual(self.run_fixture('forever').returncode, 75)
        first = self.receipt()['attempts'][0]
        self.assertEqual(self.run_fixture('success', resume=True).returncode, 0)
        receipt = self.receipt()
        self.assertEqual(len(receipt['attempts']), 2)
        self.assertEqual(receipt['attempts'][0], first)
        self.assertEqual(receipt['attempts'][1]['argv'][1:3], ['exec', 'resume'])

    def test_capacity_without_final_period_resumes_same_session(self):
        result = self.run_fixture('no_dot')
        self.assertEqual(result.returncode, 0, result.stdout)
        attempts = self.receipt()['attempts']
        self.assertEqual([a['state'] for a in attempts], ['capacity', 'native_success'])
        self.assertEqual(attempts[1]['argv'][1:3], ['exec', 'resume'])
        self.assertIn(SESSION, attempts[1]['argv'])

    def test_next_burst_waits_before_its_first_resume(self):
        self.config.update(additional_capacity_attempts=0, backoff_seconds=2)
        self.config_path.write_text(json.dumps(self.config))
        (self.root / 'mode').write_text('forever')
        spec = importlib.util.spec_from_file_location('capacity_runner_under_test', RUNNER)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        config = runner.request(self.config_path)
        self.out.mkdir()
        meta = dict(request=config, state='ready', native_session_id=None, attempts=[])
        sleeper = mock.Mock()
        # Replace the runner's clock binding; subprocess keeps its own real polling sleeps.
        clock = SimpleNamespace(monotonic=runner.time.monotonic, sleep=sleeper)
        with mock.patch.object(runner, 'time', clock):
            self.assertEqual(runner.execute(config, self.out, meta), 75)
            sleeper.assert_not_called()
            (self.root / 'mode').write_text('success')
            self.assertEqual(runner.execute(config, self.out, meta), 0)
            sleeper.assert_called_once_with(2)
        self.assertEqual(meta['attempts'][1]['argv'][1:3], ['exec', 'resume'])
        self.assertIn(SESSION, meta['attempts'][1]['argv'])

    def test_burst_must_fit_resume_backoff_before_launch(self):
        self.config.update(attempt_timeout_seconds=2, backoff_seconds=2, burst_seconds=4)
        result = self.run_fixture('success')
        self.assertEqual(result.returncode, 2)
        self.assertIn('resume backoff', result.stdout)
        self.assertFalse((self.root / 'history.json').exists())

    def test_noncapacity_and_unstructured_errors_never_retry(self):
        for mode in ['permission', 'raw', 'badjson']:
            with self.subTest(mode=mode):
                self.out = self.root / mode
                self.assertEqual(self.run_fixture(mode).returncode, 1)
                self.assertEqual(len(self.receipt()['attempts']), 1)

    def test_session_mismatch_does_not_retry_again(self):
        self.assertEqual(self.run_fixture('mismatch').returncode, 1)
        self.assertEqual(len(self.receipt()['attempts']), 2)
        self.assertEqual(self.receipt()['state'], 'session_unresolved')

    def test_changed_model_cannot_resume_old_request(self):
        self.config['additional_capacity_attempts'] = 0
        self.assertEqual(self.run_fixture('forever').returncode, 75)
        self.config['model'] = 'another-model'
        self.assertEqual(self.run_fixture('success', resume=True).returncode, 2)
        self.assertEqual(len(self.receipt()['attempts']), 1)

    def test_timeout_is_not_a_capacity_retry(self):
        self.config['attempt_timeout_seconds'] = 1
        self.assertEqual(self.run_fixture('timeout').returncode, 1)
        self.assertEqual(self.receipt()['state'], 'timeout')
        self.assertEqual(len(self.receipt()['attempts']), 1)

    def test_unfinished_command_blocks_capacity_replay(self):
        self.assertEqual(self.run_fixture('unresolved').returncode, 1)
        self.assertEqual(self.receipt()['state'], 'inflight_items')
        self.assertEqual(len(self.receipt()['attempts']), 1)


if __name__ == '__main__':
    unittest.main()
