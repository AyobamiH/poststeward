import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('restore_runtime', ROOT / 'scripts/restore-local-runtime.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class RestoreLocalRuntimeTests(unittest.TestCase):
    def simulate(self, *, busy=False, install_failure=False, refill_failure=False, dirty=False):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            code, stdout = 0, ''
            if args[:2] == ['git', 'diff'] and dirty:
                code = 1
            elif 'show' in args:
                stdout = 'active' if args[3].endswith('.timer') or busy else 'inactive'
            elif args[:1] == ['sh'] and install_failure:
                code = 1
            elif args == ['systemctl', '--user', 'start', '--no-block', helper.SERVICES[1]] and refill_failure:
                code = 1
            return subprocess.CompletedProcess(args, code, stdout=stdout, stderr='')
        return calls, run

    def test_restore_installs_requests_collection_and_refill_then_resumes_without_direct_publish(self):
        calls, run = self.simulate()
        with patch.object(helper, 'run', side_effect=run):
            self.assertEqual(helper.main(), 0)
        args = [c[0] for c in calls]
        install = next(i for i, a in enumerate(args) if a[0] == 'sh')
        collection = args.index(['systemctl', '--user', 'start', '--no-block', helper.SERVICES[2]])
        refill = args.index(['systemctl', '--user', 'start', '--no-block', helper.SERVICES[1]])
        resume = args.index(['systemctl', '--user', 'start', *helper.TIMERS])
        self.assertLess(install, collection)
        self.assertLess(collection, refill)
        self.assertLess(refill, resume)
        self.assertEqual(calls[install][1]['env']['OCPF_POST_DEFER_TIMER_START'], '1')
        self.assertNotIn('timeout', calls[refill][1])
        self.assertEqual([a for a in args if 'stop' in a], [['systemctl', '--user', 'stop', *helper.TIMERS]])
        self.assertFalse(any('publish' in a or 'run-due' in a or 'reset' in a or 'pull' in a for a in args))

    def test_long_refill_cannot_hold_release_reconciliation_open(self):
        calls, run = self.simulate()

        def fail_if_blocking_refill(args, **kwargs):
            if args == ['systemctl', '--user', 'start', helper.SERVICES[1]]:
                raise AssertionError('release reconciliation must not wait for refill completion')
            return run(args, **kwargs)

        with patch.object(helper, 'run', side_effect=fail_if_blocking_refill):
            self.assertEqual(helper.main(), 0)

        self.assertIn(
            ['systemctl', '--user', 'start', '--no-block', helper.SERVICES[1]],
            [args for args, _ in calls],
        )

    def test_running_service_is_never_killed_and_original_timers_resume(self):
        calls, run = self.simulate(busy=True)
        with patch.object(helper, 'run', side_effect=run), patch.object(helper.time, 'monotonic', side_effect=[0, 60]):
            with self.assertRaisesRegex(RuntimeError, 'still running'):
                helper.main()
        self.assertFalse(any(a[0] == 'sh' for a, _ in calls))
        self.assertEqual(calls[-1][0], ['systemctl', '--user', 'start', *helper.TIMERS])

    def test_install_failure_restores_original_wakeups_without_refill(self):
        calls, run = self.simulate(install_failure=True)
        with patch.object(helper, 'run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'installation failed'):
                helper.main()
        self.assertNotIn(['systemctl', '--user', 'start', '--no-block', helper.SERVICES[1]], [a for a, _ in calls])
        self.assertEqual(calls[-1][0], ['systemctl', '--user', 'start', *helper.TIMERS])

    def test_refill_failure_does_not_disable_future_timer_attempts(self):
        calls, run = self.simulate(refill_failure=True)
        with patch.object(helper, 'run', side_effect=run):
            self.assertEqual(helper.main(), 1)
        self.assertIn(['systemctl', '--user', 'start', *helper.TIMERS], [a for a, _ in calls])

    def test_dirty_checkout_does_not_change_timer_state(self):
        calls, run = self.simulate(dirty=True)
        with patch.object(helper, 'run', side_effect=run):
            with self.assertRaisesRegex(RuntimeError, 'Tracked checkout'):
                helper.main()
        self.assertTrue(all(a[0] == 'git' for a, _ in calls))

    def test_upgrade_handles_collector_not_previously_installed(self):
        calls, run = self.simulate()
        installed = False
        def host(args, **kwargs):
            nonlocal installed
            if args[:1] == ['sh']:
                installed = True
            if 'show' in args and args[3].startswith(('post-once-collection.', 'post-once-replies.')) and not installed:
                calls.append((args, kwargs))
                return subprocess.CompletedProcess(args, 4, stdout='not-found', stderr='')
            return run(args, **kwargs)
        with patch.object(helper, 'run', side_effect=host):
            self.assertEqual(helper.main(), 0)
        self.assertIn(['systemctl', '--user', 'stop', *helper.TIMERS[:2]], [a for a, _ in calls])
        self.assertIn(['systemctl', '--user', 'start', '--no-block', helper.SERVICES[2]], [a for a, _ in calls])
