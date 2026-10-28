from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plcforge.agent import Agent
from plcforge.benchmark import summarize
from plcforge.compiler import CodesysCompiler, source_sections
from plcforge.config import Config
from plcforge.context import compact, repair_pairing
from plcforge.ltl import evaluate, monitor, parse
from plcforge.provider import ModelError, Reply, assemble_stream
from plcforge.security import Workspace, digest, redact
from plcforge.st import Program, STError, lint, run_tests
from plcforge.tasks import load_task, task_prompt, validate_task
from plcforge.tools import ToolRegistry

ROOT = Path(__file__).resolve().parents[1]
MOTOR = (ROOT/'examples'/'reference_solutions'/'S01.st').read_text(encoding='utf-8')


def setUpModule():
    # Keep test data in the project, including in Windows AppContainer sandboxes.
    test_tmp = (ROOT/'.plcforge'/'test-temp').resolve()
    if not test_tmp.is_relative_to(ROOT.resolve()):
        raise RuntimeError('Invalid test temporary directory')
    test_tmp.mkdir(parents=True,exist_ok=True)
    tempfile.tempdir = str(test_tmp)


def call(name, args, call_id='call_1'):
    return {'role':'assistant','content':None,'tool_calls':[
        {'id':call_id,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]}


class ScriptedProvider:
    """Test fixture, never a live model or baseline framework."""
    def __init__(self, messages):
        self.messages = iter(messages)
    def complete(self, messages, tools, on_text=None, deadline=None):
        msg = next(self.messages)
        return Reply(msg,{'prompt_tokens':10,'completion_tokens':10,'total_tokens':20},
                     'tool_calls' if msg.get('tool_calls') else 'stop',0.0)


class DatasetTests(unittest.TestCase):
    def test_all_draft_reference_fixtures_and_specs(self):
        files = list((ROOT/'data'/'tasks').glob('*.json'))
        self.assertGreaterEqual(len(files),11)
        for file in files:
            with self.subTest(task=file.stem):
                task = load_task(file)
                self.assertFalse(validate_task(task))
                source = (ROOT/'examples'/'reference_solutions'/(file.stem+'.st')).read_text(encoding='utf-8')
                self.assertEqual(lint(source)['status'],'passed')
                result = run_tests(source,task['tests'])
                self.assertEqual(result['status'],'passed',result)
                ltl = monitor(task['specs'],result)
                self.assertNotIn(ltl['status'],('violated','unsupported'),ltl)
                self.assertFalse(ltl['is_formal_verification'])

    def test_emergency_priority_mutation_is_detected(self):
        task = load_task(ROOT/'data'/'tasks'/'S01.json')
        mutated = MOTOR.replace('IF StopBtn OR EStop THEN','IF StopBtn THEN')
        self.assertEqual(run_tests(mutated,task['tests'])['status'],'failed')

    def test_held_sensor_mutation_is_detected(self):
        source = (ROOT/'examples'/'reference_solutions'/'S04.st').read_text(encoding='utf-8')
        task = load_task(ROOT/'data'/'tasks'/'S04.json')
        mutated = source.replace('Sensor AND NOT Prev AND Count < 3','Sensor AND Count < 3')
        self.assertEqual(run_tests(mutated,task['tests'])['status'],'failed')

    def test_tests_need_an_oracle(self):
        self.assertEqual(run_tests(MOTOR,[{'name':'empty','scans':[{'inputs':{}}]}])['status'],'failed')


class STTests(unittest.TestCase):
    def test_unsupported_array_is_not_a_pass(self):
        source = 'PROGRAM P\nVAR A : ARRAY[0..2] OF INT; END_VAR\nEND_PROGRAM'
        self.assertEqual(lint(source)['status'],'unsupported')
    def test_unterminated_comment(self):
        self.assertEqual(lint(MOTOR+'(*')['status'],'failed')
    def test_missing_semicolon(self):
        self.assertEqual(lint(MOTOR.replace('Motor := FALSE;','Motor := FALSE'))['status'],'failed')
    def test_undeclared_assignment_is_detected_during_scan(self):
        p = Program(MOTOR.replace('Motor := FALSE;','Unknown := FALSE;'))
        with self.assertRaises(STError):
            p.scan({'StopBtn':True})
    def test_bool_numeric_type_mismatch(self):
        p = Program(MOTOR.replace('Motor := TRUE;','Motor := 1;'))
        with self.assertRaises(STError):
            p.scan({'StartBtn':True})
    def test_scan_loop_budget(self):
        p = Program('PROGRAM P VAR i : INT; x : INT; END_VAR FOR i := 1 TO 3000 DO x := x+1; END_FOR; END_PROGRAM')
        with self.assertRaises(STError):
            p.scan({})
    def test_state_resets_between_tests(self):
        result = run_tests(MOTOR,[{'name':'a','scans':[{'inputs':{'StartBtn':True},'expect':{'Motor':True}}]},
                                 {'name':'b','scans':[{'inputs':{},'expect':{'Motor':False}}]}])
        self.assertEqual(result['passed'],2)
    def test_false_expected_is_not_zero(self):
        self.assertEqual(run_tests(MOTOR,[{'scans':[{'inputs':{},'expect':{'Motor':0}}]}])['status'],'failed')
    def test_timer_reset_and_exact_boundary(self):
        task = load_task(ROOT/'data'/'tasks'/'S06.json')
        source = (ROOT/'examples'/'reference_solutions'/'S06.st').read_text(encoding='utf-8')
        self.assertEqual(run_tests(source,task['tests'])['status'],'passed')
    def test_integer_division_truncates_toward_zero(self):
        p = Program('PROGRAM P VAR x : INT; END_VAR x := -5 / 2; END_PROGRAM')
        self.assertEqual(p.scan({})['X'],-2)
    def test_tof_first_falling_scan(self):
        p = Program('PROGRAM P VAR_INPUT a : BOOL; END_VAR VAR_OUTPUT q : BOOL; END_VAR VAR t : TOF; END_VAR t(IN := a, PT := T#40ms); q := t.Q; END_PROGRAM')
        self.assertTrue(p.scan({'a':True})['Q'])
        self.assertTrue(p.scan({'a':False})['Q'])
        self.assertTrue(p.scan({})['Q'])
        self.assertFalse(p.scan({})['Q'])
    def test_ctu_held_clock(self):
        p = Program('PROGRAM P VAR_INPUT a : BOOL; END_VAR VAR_OUTPUT n : INT; END_VAR VAR c : CTU; END_VAR c(CU := a, RESET := FALSE, PV := 3); n := c.CV; END_PROGRAM')
        self.assertEqual(p.scan({'a':True})['N'],1)
        self.assertEqual(p.scan({})['N'],1)
    def test_forward_for(self):
        p = Program('PROGRAM P VAR x : INT; i : INT; END_VAR FOR i := 1 TO 3 DO x := x+i; END_FOR; END_PROGRAM')
        self.assertEqual(p.scan({})['X'],6)
    def test_numeric_if_condition_is_rejected(self):
        p = Program('PROGRAM P VAR x : INT; END_VAR IF 1 THEN x := 1; END_IF; END_PROGRAM')
        with self.assertRaises(STError):
            p.scan({})
    def test_exponent_execution_bound(self):
        result = run_tests('PROGRAM P VAR_OUTPUT x : LINT; END_VAR x := 2 ** 999999; END_PROGRAM',
                           [{'name':'bound','scans':[{'expect':{'x':0}}]}])
        self.assertEqual(result['status'],'unsupported')


class LTLTests(unittest.TestCase):
    def test_bad_safety_trace_is_a_violation(self):
        self.assertFalse(evaluate(parse('G (Stop -> !Motor)'),[{'STOP':True,'MOTOR':True}],0))
    def test_missing_future_is_inconclusive(self):
        self.assertIsNone(evaluate(parse('X Motor'),[{'MOTOR':False}],0))
    def test_unseen_eventuality_is_inconclusive(self):
        self.assertIsNone(evaluate(parse('F Motor'),[{'MOTOR':False}],0))
    def test_until(self):
        self.assertTrue(evaluate(parse('A U B'),[{'A':True,'B':False},{'A':False,'B':True}],0))
        self.assertFalse(evaluate(parse('A U B'),[{'A':False,'B':False}],0))
    def test_missing_proposition_is_unsupported(self):
        value = monitor([{'id':'P','formula':'G Missing'}],{'cases':[{'name':'x','trace':[{}]}]})
        self.assertEqual(value['status'],'unsupported')


class SecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.workspace = Workspace(Path(self.tmp.name))
    def tearDown(self):
        self.tmp.cleanup()
    def test_traversal(self):
        for path in ('../outside.st','C:/outside.st','src/../../outside.st','.env','a/.env','a/.git/config.json',
                     '.plcforge/sessions/x.json','src/password.json','src/evil.py'):
            with self.subTest(path=path),self.assertRaises(ValueError):
                self.workspace.path(path)
    def test_checkpoints_and_stale_edit(self):
        registry = ToolRegistry(self.workspace,CodesysCompiler(Config().codesys),{})
        registry.execute('write_file',{'path':'src/x.st','content':'old'})
        registry.execute('write_file',{'path':'src/x.st','content':'new'})
        self.assertTrue((self.workspace.root/'.plcforge'/'checkpoints'/(digest('old')+'.txt')).exists())
        with self.assertRaises(ValueError):
            registry.execute('edit_file',{'path':'src/x.st','old':'new','new':'next','expected_sha256':digest('old')})
    def test_unknown_tool_and_extra_args(self):
        registry = ToolRegistry(self.workspace,CodesysCompiler(Config().codesys),{})
        with self.assertRaises(ValueError): registry.execute('shell',{'command':'whoami'})
        with self.assertRaises(ValueError): registry.execute('compile_st',{'command':'anything'})
    def test_key_redaction(self):
        with patch.dict(os.environ,{'UNIT_TEST_API_KEY':'testcredential1234'}):
            self.assertEqual(redact('key testcredential1234'),'key [REDACTED]')
            with self.assertRaises(ValueError):
                self.workspace.write('output.txt','testcredential1234')
    def test_bound_tests_cannot_be_overridden(self):
        task = load_task(ROOT/'data'/'tasks'/'S01.json')
        registry = ToolRegistry(self.workspace,CodesysCompiler(Config().codesys),{},task)
        self.workspace.write('src/PLC_PRG.st',MOTOR.replace('Motor := TRUE;','Motor := FALSE;'))
        self.workspace.write('tests/fake.json',json.dumps({'tests':[{'scans':[{'expect':{'Motor':False}}]}]}))
        result = registry.execute('test_st',{'path':'src/PLC_PRG.st','tests_path':'tests/fake.json'})
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['test_origin'],'bound_task')


class ProviderContextTests(unittest.TestCase):
    def test_stream_tool_fragments_reasoning_and_usage(self):
        events = [
            {'choices':[{'delta':{'reasoning_content':'plan','tool_calls':[{'index':0,'id':'id_1','function':{'name':'write_file','arguments':'{"path":'}}]}}]},
            {'choices':[{'delta':{'content':'ok','tool_calls':[{'index':0,'function':{'arguments':'"x.st","content":"a"}'}}]},'finish_reason':'tool_calls'}]},
            {'choices':[],'usage':{'total_tokens':15}}]
        msg,usage,finish = assemble_stream(events)
        self.assertEqual(msg['reasoning_content'],'plan')
        self.assertEqual(json.loads(msg['tool_calls'][0]['function']['arguments'])['path'],'x.st')
        self.assertEqual(usage['total_tokens'],15)
    def test_interrupted_stream_is_rejected(self):
        with self.assertRaises(ModelError): assemble_stream([{'choices':[{'delta':{'content':'partial'}}]}])
    def test_dangling_call_repaired(self):
        messages = repair_pairing([{'role':'user','content':'a'},call('list_files',{})])
        self.assertEqual(messages[-1]['tool_call_id'],'call_1')
    def test_compaction_preserves_round_pairing(self):
        messages = [{'role':'system','content':'system'},{'role':'user','content':'task'}]
        for i in range(8):
            messages += [call('list_files',{},str(i)),{'role':'tool','tool_call_id':str(i),'content':'x'*100}]
        kept,changed = compact(messages,10,{})
        self.assertTrue(changed)
        self.assertEqual(kept[1]['content'],'task')
        self.assertEqual(kept,repair_pairing(kept))


class CompilerAgentTests(unittest.TestCase):
    def test_no_compiler_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = CodesysCompiler(Config().codesys).compile({'a.st':MOTOR},Path(tmp))
        self.assertEqual(result['status'],'unavailable')
    def test_installing_compiler_invalidates_an_old_unavailable_result(self):
        task = load_task(ROOT/'data'/'tasks'/'S01.json')
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace(Path(tmp))
            workspace.write('src/PLC_PRG.st',MOTOR)
            hashes = {'src/PLC_PRG.st':digest(MOTOR)}
            state = {'validation':{'compile':{'status':'unavailable','source_hashes':hashes,
                                              'compiler_fingerprint':'before-installation'}}}
            compiler = CodesysCompiler(Config().codesys)
            with patch.object(compiler,'fingerprint',return_value='after-installation'), \
                    patch.object(compiler,'compile',return_value={'status':'passed','source_hashes':hashes,
                                                                 'compiler_fingerprint':'after-installation'}) as build:
                gate = ToolRegistry(workspace,compiler,state,task).final_gate()
                self.assertEqual(gate['status'],'completed')
                build.assert_called_once()
    def test_cannot_split_multiple_pous(self):
        with self.assertRaises(STError): source_sections(MOTOR+MOTOR)
    def test_bridge_splits_declaration_and_body(self):
        parts = source_sections(MOTOR)
        self.assertTrue(parts['declaration'].endswith('END_VAR'))
        self.assertNotIn('END_PROGRAM',parts['implementation'])
        self.assertEqual(parts['name'],'PLC_PRG')
    def test_agent_writes_repairs_and_stops_with_pending_compiler(self):
        task = load_task(ROOT/'data'/'tasks'/'S01.json')
        bad = MOTOR.replace('Motor := TRUE;','Motor := FALSE;')
        provider = ScriptedProvider([call('write_file',{'path':'src/PLC_PRG.st','content':bad}),
            {'role':'assistant','content':'done'},call('write_file',{'path':'src/PLC_PRG.st','content':MOTOR}),
            {'role':'assistant','content':'generated; compile pending'}])
        with tempfile.TemporaryDirectory() as tmp:
            result = Agent(Config(max_turns=6),Path(tmp),task,provider).run(task_prompt(task))
            self.assertEqual(result['status'],'awaiting_codesys',result)
            self.assertEqual(result['validation']['tests']['status'],'passed')
            self.assertTrue((Path(tmp)/'src'/'PLC_PRG.st').is_file())
            session = result['session_id']
            resumed = Agent(Config(),Path(tmp),provider=ScriptedProvider([
                {'role':'assistant','content':'revalidated'}]),session_id=session).run('Recheck')
            self.assertEqual(resumed['status'],'awaiting_codesys')
    def test_unknown_tools_are_recoverable(self):
        provider = ScriptedProvider([call('bogus',{}),call('bogus',{})])
        with tempfile.TemporaryDirectory() as tmp:
            result = Agent(Config(max_turns=2),Path(tmp),provider=provider).run('motor')
            self.assertEqual(result['status'],'max_turns')
            self.assertEqual(result['tool_calls'],2)
    def test_finish_tool_runs_completion_gate(self):
        task = load_task(ROOT/'data'/'tasks'/'S01.json')
        provider = ScriptedProvider([call('write_file',{'path':'src/PLC_PRG.st','content':MOTOR}),
            call('finish_task',{'summary':'Source verified; CODESYS pending'})])
        with tempfile.TemporaryDirectory() as tmp:
            result = Agent(Config(max_turns=3),Path(tmp),task,provider).run(task_prompt(task))
            self.assertEqual(result['status'],'awaiting_codesys')
            self.assertEqual(result['model_calls'],2)
    def test_finish_before_a_later_write_does_not_approve_mutation(self):
        task = load_task(ROOT/'data'/'tasks'/'S01.json')
        msg = call('finish_task',{'summary':'done'},'finish')
        msg['tool_calls'] += call('write_file',{'path':'src/PLC_PRG.st','content':MOTOR.replace('Motor := TRUE;','Motor := FALSE;')},'write')['tool_calls']
        provider = ScriptedProvider([call('write_file',{'path':'src/PLC_PRG.st','content':MOTOR}),msg])
        with tempfile.TemporaryDirectory() as tmp:
            result = Agent(Config(max_turns=2),Path(tmp),task,provider).run(task_prompt(task))
            self.assertEqual(result['status'],'max_turns')
    def test_missing_compile_metric_is_null(self):
        records = [{'framework':'fixture','model_label':'A','score':{'compile':{'status':'unavailable'},
                   'tests':{'status':'passed','passed':1,'total':1}}}]
        row = summarize(records)['groups'][0]
        self.assertIsNone(row['final_compile_pass_rate'])
        self.assertIsNone(row['formal_verification_pass_rate'])
        self.assertIsNone(row['total_tokens'])


if __name__ == '__main__':
    unittest.main()
