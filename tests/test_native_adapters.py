"""Opt-in SDK checks against a real synthetic CLI process; never call an LLM."""
import json
import os
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))


class NativeAdapters(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cli = pathlib.Path(self.tmp.name) / 'fake-plazmi'
        self.log = pathlib.Path(self.tmp.name) / 'calls.jsonl'
        self.cli.write_text(
            '#!' + sys.executable + '\n'
            'import json, os, sys\n'
            'mode=sys.argv[-1]\n'
            'with open(os.environ["TEST_CALL_LOG"],"a") as f: f.write(json.dumps(sys.argv[1:])+"\\n")\n'
            'exit_code=int(os.environ.get("TEST_CLI_EXIT","0"))\n'
            'value={"ok":False,"error":"synthetic semantic rejection"} if exit_code==1 else ({"notable":int(os.environ.get("TEST_NOTABLE","0"))} if mode=="pulse" else {"you_noted":["synthetic note"]})\n'
            'print(json.dumps(value))\n'
            'sys.exit(exit_code)\n')
        self.cli.chmod(0o700)
        self.old_env = {k: os.environ.get(k) for k in ['TEST_CALL_LOG','TEST_NOTABLE','TEST_CLI_EXIT']}
        os.environ['TEST_CALL_LOG'] = str(self.log)
        os.environ['TEST_NOTABLE'] = '0'
        os.environ['TEST_CLI_EXIT'] = '0'

    def tearDown(self):
        for k,v in self.old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def calls(self):
        return [json.loads(l) for l in self.log.read_text().splitlines()]

    def test_smolagents_tool_invokes_only_pulse(self):
        from adapters.smolagents_pulse import PlazmiPulse
        value = json.loads(PlazmiPulse(str(self.cli))())
        self.assertEqual(0, value['cli_exit_code'])
        self.assertEqual([['--json','pulse']], self.calls())

    def test_langgraph_quiet_pulse_stops(self):
        from adapters.langgraph_catchup import build_graph
        state = build_graph().invoke({'cli':str(self.cli)})
        self.assertNotIn('digest', state)
        self.assertEqual([['--json','pulse']], self.calls())

    def test_langgraph_notable_pulse_reads_digest_once(self):
        from adapters.langgraph_catchup import build_graph
        os.environ['TEST_NOTABLE'] = '1'
        state = build_graph().invoke({'cli':str(self.cli)})
        self.assertEqual(['synthetic note'], state['digest']['you_noted'])
        self.assertEqual([['--json','pulse'], ['--json','digest']], self.calls())

    def test_langgraph_failed_pulse_does_not_continue(self):
        from adapters.langgraph_catchup import build_graph
        os.environ['TEST_CLI_EXIT'] = '2'
        state = build_graph().invoke({'cli':str(self.cli)})
        self.assertEqual(2, state['exit_code'])
        self.assertNotIn('digest', state)
        self.assertEqual([['--json','pulse']], self.calls())

    def test_langgraph_semantic_rejection_does_not_continue(self):
        from adapters.langgraph_catchup import build_graph
        os.environ['TEST_CLI_EXIT'] = '1'
        os.environ['TEST_NOTABLE'] = '1'
        state = build_graph().invoke({'cli':str(self.cli)})
        self.assertEqual(1, state['exit_code'])
        self.assertFalse(state['pulse']['ok'])
        self.assertNotIn('digest', state)
        self.assertEqual([['--json','pulse']], self.calls())


if __name__ == '__main__':
    unittest.main()
