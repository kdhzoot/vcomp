#!/usr/bin/env python3
"""Runner-only regression checks; no database or storage benchmark is opened."""
import argparse
import os
import signal
import subprocess
import sys
import unittest
from unittest.mock import patch

import run_ycsb_alternatives as runner


class RunnerTest(unittest.TestCase):
    def campaign(self):
        return runner.Campaign(argparse.Namespace(
            run_id='test_only_no_launch', exclude_flush_only=True,
            reuse_run=None, duration=300))

    def test_selected_cells(self):
        campaign = self.campaign()
        self.assertEqual(len(campaign.cells), 30)
        self.assertEqual(len(set(campaign.cells)), 30)
        self.assertNotIn('flush_only', campaign.systems)
        remaining = [c for c in campaign.cells if c != ('baseline', 'workloada')]
        self.assertEqual(len(remaining), 29)
        self.assertEqual(remaining[0], ('last_comp', 'workloada'))
        for system, workload in campaign.cells:
            opts = runner.options(campaign.sources[system + '_1kb'],
                                  campaign.dbroot, campaign.root, workload, 300)
            self.assertEqual(opts['duration'], 300)
            self.assertFalse(opts['readonly'])
            self.assertFalse(opts['disable_auto_compactions'])
            self.assertEqual(opts['cache_size'], 1)

    def test_zombies_do_not_count_as_active_benchmarks(self):
        with patch.object(runner, 'all_benchmarks', return_value=[1, 2, 3, 4]), \
                patch.object(runner, 'process_state',
                             side_effect=[('S', 10), ('Z', 10), None, ('D', 10)]):
            self.assertEqual(runner.active_benchmarks(), [1, 4])

    def check_termination(self, wrapper_exits_first):
        child_code = ('import signal,time; '
                      'signal.signal(signal.SIGTERM, signal.SIG_IGN); '
                      'print("ready", flush=True); time.sleep(60)')
        wrapper_code = (
            'import subprocess,sys,time\n'
            f'child=subprocess.Popen([sys.executable,"-c",{child_code!r}], '
            'stdout=subprocess.PIPE,text=True)\n'
            'assert child.stdout.readline().strip()=="ready"\n'
            'print(child.pid,flush=True)\n' +
            ('' if wrapper_exits_first else 'time.sleep(60)\n'))
        campaign = self.campaign()
        proc = subprocess.Popen([sys.executable, '-c', wrapper_code],
                                stdout=subprocess.PIPE, text=True,
                                start_new_session=True)
        campaign.proc = proc
        try:
            child_pid = int(proc.stdout.readline())
            if wrapper_exits_first:
                proc.wait(timeout=5)
            self.assertIn(child_pid, runner.group_members(proc.pid))
            campaign.terminate(grace_seconds=1)
            self.assertEqual(runner.group_members(proc.pid), [])
            self.assertIsNotNone(proc.poll())
        finally:
            if runner.group_members(proc.pid):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.wait(timeout=5)
            proc.stdout.close()

    def test_termination_waits_for_child_when_wrapper_is_alive(self):
        self.check_termination(wrapper_exits_first=False)

    def test_termination_waits_for_child_after_wrapper_exit(self):
        self.check_termination(wrapper_exits_first=True)


if __name__ == '__main__':
    unittest.main()
