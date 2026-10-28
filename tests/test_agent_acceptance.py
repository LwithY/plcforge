"""Regression coverage for the 2026-10-28 handoff acceptance criteria."""
from __future__ import annotations

import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import ssl
import unittest
from unittest.mock import patch

from plcforge.agent import Agent
from plcforge.budget import account, initialize, preflight
from plcforge.compiler import CodesysCompiler
from plcforge.config import Config
from plcforge.context import compact, repair_pairing
from plcforge.evaluation import evaluate
from plcforge.progress import TerminalReporter
from plcforge.security import Workspace, digest
from plcforge.st import check_interface, lint, run_tests
from plcforge.tasks import load_task, task_prompt, validate_task
from plcforge.tools import ToolRegistry
from test_system import MOTOR, ScriptedProvider, call
from test_conversation_transport import FakeResponse, response

ROOT = Path(__file__).resolve().parents[1]


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT/'.plcforge/test-temp'
        parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.temp.name)
        self.workspace = Workspace(self.root/'model')
        self.task = load_task(ROOT/'data/tasks/S01.json')
        self.state = {}
        self.registry = ToolRegistry(self.workspace, CodesysCompiler(Config().codesys), self.state, self.task)
        self.registry.execute('write_file', {'path':'src/PLC_PRG.st','content':MOTOR})

    def tearDown(self):
        self.temp.cleanup()

    def test_pass_feedback_under_2kb_full_trace_on_disk(self):
        result = self.registry.execute('test_st', {'path':'src/PLC_PRG.st'})
        self.assertLess(len(json.dumps(result,ensure_ascii=False).encode()), 2000)
        self.assertNotIn('"trace":', json.dumps(result))
        report = json.loads((self.workspace.root/result['report_path']).read_text(encoding='utf-8'))
        self.assertTrue(report['cases'][0]['trace'])
        self.assertEqual(report['source_hashes'], {'src/PLC_PRG.st':digest(MOTOR)})
        self.assertIn('engine_version', result)

    def test_failure_identifies_first_scan_inputs_expected_actual(self):
        self.registry.execute('write_file', {'path':'src/PLC_PRG.st','content':MOTOR.replace('Motor := TRUE;', 'Motor := FALSE;')})
        result = self.registry.execute('test_st', {'path':'src/PLC_PRG.st'})
        failure = result['failed_cases'][0]
        self.assertEqual(result['status'], 'failed')
        self.assertIn('inputs', failure)
        self.assertIn('expected', failure['failure'])
        self.assertIn('actual', failure['failure'])
        self.assertIsInstance(failure['first_divergence_scan'], int)

    def test_evidence_is_paged_and_private_paths_stay_blocked(self):
        result = self.registry.execute('test_st', {'path':'src/PLC_PRG.st'})
        page = self.registry.execute('read_evidence', {'report_id':result['report_id'], 'max_lines':3})
        self.assertEqual(len(page['content'].splitlines()),3)
        with self.assertRaises(ValueError):
            self.registry.execute('read_evidence', {'report_id':'../holdout'})
        with self.assertRaises(ValueError):
            self.workspace.path(result['report_path'])

    def test_large_compaction_strictly_shrinks_preserves_pairing_and_all_updates(self):
        messages = [{'role':'system','content':'system'},{'role':'user','content':'original'}]
        for i in range(7):
            messages += [{'role':'user','content':'required supplement %d'%i}, call('test_st', {'path':'a.st'},str(i)),
                {'role':'tool','tool_call_id':str(i),'content':json.dumps({'cases':[{'trace':[{'x':True}]*800}]})}]
        state = {'validation':{'tests':{'cases':[{'trace':[1]*10000}]}}, 'context_archive_dir':str(self.root/'archive')}
        kept, changed = compact(messages, 2000, state)
        self.assertTrue(changed)
        self.assertLess(state['context_measurements'][-1]['after_chars'], state['context_measurements'][-1]['before_chars'])
        self.assertEqual(kept, repair_pairing(kept))
        for i in range(7):
            self.assertIn('required supplement %d'%i, json.dumps(kept))
        self.assertTrue(Path(state['context_measurements'][-1]['archive_path']).is_file())

    def test_compaction_never_replaces_with_larger_checkpoint(self):
        messages = [{'role':'system','content':'s'},{'role':'user','content':'u'}]
        kept, changed = compact(messages, 1, {'validation':{'x':'v'*9000}}, force=True)
        self.assertFalse(changed)
        self.assertEqual(kept,messages)

    def test_host_archives_after_budget_stop_without_extra_request(self):
        provider = ScriptedProvider([call('write_file', {'path':'src/PLC_PRG.st','content':MOTOR})])
        agent = Agent(Config(), self.root/'run', self.task, provider)
        def build(sources, output, deadline):
            # Unit fixture only. Native compilation has its own integration evidence.
            return {'status':'passed','engine':'unit-fixture','source_hashes':{p:digest(s) for p,s in sources.items()},
                    'compiler_fingerprint':agent.registry.compiler.fingerprint()}
        from plcforge.provider import Reply
        def exhausting(*args, **kwargs):
            return Reply(call('write_file', {'path':'src/PLC_PRG.st','content':MOTOR}),
                         {'prompt_tokens':159990,'completion_tokens':20,'total_tokens':160010}, 'tool_calls',0)
        with patch.object(provider,'complete',side_effect=exhausting) as request, patch.object(agent.registry.compiler,'compile',side_effect=build):
            result = agent.run(task_prompt(self.task))
        self.assertEqual(request.call_count,1)
        self.assertEqual(result['stop_reason'],'token_budget')
        self.assertEqual(result['artifact_status'],'completed')
        self.assertEqual(result['report_status'],'saved')
        self.assertTrue((Path(result['artifact']['directory'])/'src/PLC_PRG.st').is_file())

    def test_source_and_test_changes_invalidate_old_evidence(self):
        first = self.registry.execute('test_st', {'path':'src/PLC_PRG.st'})
        second = self.registry.execute('test_st', {'path':'src/PLC_PRG.st'})
        self.assertEqual(first['report_id'], second['report_id'])
        self.assertEqual(len(self.state['test_history']),1)
        self.registry.execute('write_file', {'path':'src/PLC_PRG.st','content':MOTOR+'\n'})
        changed = self.registry.execute('test_st', {'path':'src/PLC_PRG.st'})
        self.assertNotEqual(first['report_id'],changed['report_id'])
        self.task['scan_ms'] += 1
        scanned = self.registry.execute('test_st', {'path':'src/PLC_PRG.st'})
        self.assertNotEqual(changed['report_id'],scanned['report_id'])
        self.task['tests'][0]['name'] += '_new_version'
        renamed = self.registry.execute('test_st', {'path':'src/PLC_PRG.st'})
        self.assertNotEqual(scanned['report_id'],renamed['report_id'])

    def test_engine_version_invalidates_tests(self):
        first = self.registry.execute('test_st', {'path':'src/PLC_PRG.st'})
        with patch('plcforge.tools.ENGINE_VERSION','changed-engine'):
            second = self.registry.execute('test_st', {'path':'src/PLC_PRG.st'})
        self.assertNotEqual(first['report_id'],second['report_id'])

    def test_unavailable_and_unsupported_never_complete(self):
        self.assertEqual(self.registry.final_gate()['status'],'awaiting_codesys')
        self.registry.execute('write_file', {'path':'src/PLC_PRG.st','content':MOTOR.replace('VAR\n','VAR\n a : ARRAY[0..2] OF INT;\n',1)})
        self.assertNotEqual(self.registry.final_gate()['status'],'completed')

    def test_unknown_compile_status_never_completes(self):
        with patch.object(self.registry.compiler,'compile',return_value={'status':'not_run'}):
            self.assertEqual(self.registry.final_gate()['status'],'generated_validation_incomplete')

    def test_missing_tests_never_complete(self):
        self.registry.task = None
        self.assertEqual(self.registry.final_gate()['status'],'needs_repair')

    def test_preflight_blocks_before_network_and_saves_session(self):
        provider = ScriptedProvider([])
        agent = Agent(Config(total_token_budget=1), self.root/'blocked', provider=provider)
        result = agent.run('Implement motor latch',mode='code')
        self.assertEqual(result['status'],'token_budget')
        self.assertEqual(result['model_calls'],0)
        self.assertFalse(result['last_preflight']['allowed'])
        self.assertTrue(agent.state_path.is_file())

    def test_preflight_output_cap_respects_request_and_session_limits(self):
        config = Config(total_token_budget=2000, request_token_budget=1000,max_output_tokens=8192)
        state = {'estimated_budget_tokens':500}
        initialize(state,config)
        result = preflight(state,config,[{'role':'user','content':'Q'}],[])
        self.assertTrue(result['allowed'])
        self.assertEqual(result['estimated_input_tokens']+result['reserved_output_tokens'],1000)

    def test_cross_request_and_resume_keep_cumulative_budget(self):
        agent = Agent(Config(),self.root/'session',provider=ScriptedProvider([{'role':'assistant','content':'one'}, {'role':'assistant','content':'two'}]))
        first = agent.run('Explain PLC scans')
        second = agent.run('Explain TON')
        self.assertEqual(second['usage']['total_tokens'],40)
        resumed = Agent(Config(total_token_budget=999999),self.root/'session',session_id=first['session_id'],provider=ScriptedProvider([{'role':'assistant','content':'three'}]))
        result = resumed.run('Explain TOF')
        self.assertEqual(result['usage']['total_tokens'],60)
        self.assertEqual(result['budget_policy']['total_token_budget'],160000)

    def test_explicit_resume_replacement_retains_consumed_usage(self):
        agent = Agent(Config(),self.root/'replace',provider=ScriptedProvider([{'role':'assistant','content':'ok'}]))
        first = agent.run('Explain scans')
        resumed = Agent(Config(total_token_budget=200000),self.root/'replace',session_id=first['session_id'],replace_budget=True)
        self.assertEqual(resumed.state['estimated_budget_tokens'],20)
        self.assertEqual(resumed.config.total_token_budget,200000)
        self.assertTrue(resumed.state['budget_changes'])

    def test_missing_usage_and_retry_charge_are_estimates_not_bills(self):
        state = {'usage':{}}
        config = Config()
        initialize(state,config)
        estimate = preflight(state,config,[{'role':'user','content':'Q'}],[])
        missing = account(state,estimate,failed_attempt=True,usage_complete=False)
        self.assertEqual(missing['charge_basis'],'conservative_reservation_estimate')
        self.assertIsNone(missing['cash_cost'])
        self.assertEqual(state['usage'],{})
        reported = account(state,estimate, {'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,'prompt_tokens_details':{'cached_tokens':80}})
        self.assertEqual(reported['raw_api_usage']['total_tokens'],120)
        self.assertEqual(state['cached_input_tokens'],80)
        self.assertTrue(state['usage_missing'])

    def test_retry_rechecks_budget_and_caps_real_wire_output(self):
        agent = Agent(Config(total_token_budget=30000,max_output_tokens=30000,max_retries=2,api_key_env=''),
                      self.root/'retry')
        failed = FakeResponse([],fail=ssl.SSLEOFError(8,'EOF occurred'))
        with patch('plcforge.provider.request.urlopen',return_value=failed) as send, patch('plcforge.provider.time.sleep'):
            result = agent.run('Explain the PLC scan cycle',mode='answer')
        self.assertEqual(send.call_count,1)
        self.assertEqual(result['stop_reason'],'token_budget')
        self.assertEqual(result['remaining_estimated_tokens'],0)
        body = json.loads(send.call_args.args[0].data)
        self.assertLess(body['max_tokens'],30000)
        self.assertEqual(body['max_tokens'],result['usage_records'][0]['output_reservation'])
        self.assertTrue(result['usage_records'][0]['failed_attempt'])

    def test_changed_source_invalidates_previous_archive(self):
        gate = self.registry.final_gate()
        self.registry.archive(gate)
        self.registry.execute('write_file',{'path':'src/PLC_PRG.st','content':MOTOR+'\n'})
        self.assertNotIn('artifact',self.state)

    def test_unterminated_comment_stays_repairable(self):
        self.registry.execute('write_file',{'path':'src/PLC_PRG.st','content':MOTOR+'(*'})
        self.assertEqual(self.registry.final_gate()['status'],'needs_repair')

    def test_extensions_expose_subset_limits(self):
        fixtures = json.loads((ROOT/'evaluation/extensions.json').read_text(encoding='utf-8'))
        for case in fixtures['cases']:
            with self.subTest(case=case['id']):
                result = run_tests(case['sources']['src/PLC_PRG.st'],case['tests'])
                self.assertEqual(result['status'],case['local_status'])

    def test_interface_type_and_missing_declaration_are_distinct(self):
        source = MOTOR.replace('StartBtn : BOOL','StartBtn : INT')
        result = check_interface(source,self.task['interface'])
        self.assertEqual(result['diagnostics'][0]['expected'],'VAR_INPUT BOOL')
        self.assertEqual(result['diagnostics'][0]['actual'],'INT VAR_INPUT')
        missing = check_interface('PROGRAM PLC_PRG END_PROGRAM',self.task['interface'])
        self.assertEqual(missing['diagnostics'][0]['actual'],'<missing>')

    def test_end_if_and_case_missing_semicolons_have_precise_locations(self):
        for statement in ('IF TRUE THEN Motor := TRUE; END_IF','CASE 0 OF 0: Motor := TRUE; END_CASE'):
            source = 'PROGRAM P\nVAR_OUTPUT Motor : BOOL; END_VAR\n'+statement+'\nMotor := FALSE;\nEND_PROGRAM'
            result = lint(source,'src/example.st')
            item = result['diagnostics'][0]
            self.assertEqual(result['status'],'failed')
            self.assertEqual(item['line'],3)
            self.assertEqual(item['expected'],';')
            self.assertEqual(item['file'],'src/example.st')
            self.assertTrue(item['location_exact'])

    def test_interface_direction_mismatch_at_declaration(self):
        source = MOTOR.replace('VAR_INPUT','VAR')
        result = check_interface(source,self.task['interface'])
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['diagnostics'][0]['code'],'ST_INTERFACE')
        self.assertEqual(result['diagnostics'][0]['category'],'test_interface')
        self.assertGreater(result['diagnostics'][0]['line'],1)

    def test_saved_but_invalid_is_visible_and_history_links_revisions(self):
        bad = MOTOR.replace('END_IF;','END_IF')
        first = self.registry.execute('write_file',{'path':'src/PLC_PRG.st','content':bad})
        second = self.registry.execute('write_file',{'path':'src/PLC_PRG.st','content':bad+'\n'})
        stream = io.StringIO()
        TerminalReporter(stream)('tool_result',{'name':'write_file','result':first})
        self.assertIn('文件已保存；语法检查：failed',stream.getvalue())
        self.assertTrue(second['unresolved_diagnostics'])
        self.assertEqual(self.state['diagnostic_history'][-1]['previous_sha256'],digest(bad))
        self.assertIn('diff',self.state['diagnostic_history'][-1])

    def test_contract_and_frozen_hash_are_in_task_prompt_without_oracle(self):
        task = load_task(ROOT/'data/tasks/P01.json')
        self.assertEqual(task['contract']['review_status'],'pending_confirmation')
        self.assertNotIn('phase_5_inputs_',task_prompt(task))
        task['tests'][0]['name'] += 'changed'
        self.assertIn('Development suite hash mismatch', ';'.join(validate_task(task)))

    def test_independent_boundary_hash_and_mutations(self):
        task = load_task(ROOT/'data/tasks/P01.json')
        holdout = ROOT/'evaluation/P01/holdout.json'
        sha = hashlib.sha256(holdout.read_bytes()).hexdigest()
        source = (ROOT/'examples/reference_solutions/P01.st').read_text(encoding='utf-8')
        self.registry.task = task
        self.registry.execute('write_file',{'path':'src/PLC_PRG.st','content':source})
        report = evaluate(self.workspace.root,task,Config(),holdout,sha,self.root/'independent.json',run_result={'status':'token_budget'})
        self.assertEqual(report['framework_stop_reason'],'token_budget')
        self.assertEqual(report['independent_checks']['passed'],387)
        self.assertFalse(report['model_visibility']['independent_results_fed_back'])
        with self.assertRaises(ValueError):
            evaluate(self.workspace.root,task,Config(),holdout,'0'*64,self.root/'bad.json')
        with self.assertRaises(ValueError):
            evaluate(self.workspace.root,task,Config(),holdout,sha,self.workspace.root/'visible.json')
        cases = json.loads(holdout.read_text(encoding='utf-8'))['tests']
        mutations = [source.replace('phase := 2;', 'phase := 3;'),
                     source.replace('Filling_Valve := phase = 2;', 'Filling_Valve := phase = 2 OR phase = 1;'),
                     source.replace('phase : INT', 'queued : BOOL := FALSE;\n    phase : INT').replace(
                         'CASE phase OF', 'queued := queued OR cleaningConfirmButton;\nCASE phase OF').replace(
                         '1: IF cleaningConfirmButton', '1: IF queued')]
        for mutated in mutations:
            self.assertEqual(run_tests(mutated,cases)['status'],'failed')


if __name__=='__main__':
    unittest.main()
