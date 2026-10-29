"""Protocol fixtures only. These tests do not execute a real CODESYS compiler."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plcforge.bridge.codesys_build import diagnostic_origin, probe_source
from plcforge.compiler import CodesysCompiler, codesys_command, source_sections

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'PROGRAM PLC_PRG\nVAR x : BOOL; END_VAR\nx := TRUE;\nEND_PROGRAM'
IN_OUT_SOURCE = ('FUNCTION_BLOCK ReviewInOut\nVAR_INPUT\n    Enable : BOOL;\nEND_VAR\n'
                 'VAR_IN_OUT\n    Value : INT;\nEND_VAR\nIF Enable THEN Value := Value + 1; END_IF;\n'
                 'END_FUNCTION_BLOCK')


class FakeProcess:
    mode = 'valid'
    def __init__(self, argv, **kwargs):
        self.pid, self.returncode = 12345, 0
        job_path = Path(kwargs['env']['PLCFORGE_CODESYS_JOB'])
        job = json.loads(job_path.read_text(encoding='utf-8'))
        assert 'MODELSCOPE_API_KEY' not in kwargs['env']
        assert '--noUI' in argv
        assert '--runscript=' in argv if isinstance(argv,str) else any(x.startswith('--runscript=') for x in argv)
        if self.mode=='missing':
            return
        result = {'job_id':job['job_id'],'source_hashes':job['source_hashes'],
                  'status':'passed','build_completed':True,'generate_code_completed':True,'error_count':0}
        if self.mode=='stale': result['job_id'] = 'old_job'
        if self.mode=='not_built': result['build_completed'] = False
        if self.mode=='errors': result['error_count'] = 2
        if self.mode=='not_generated': result['generate_code_completed'] = False
        Path(job['result']).write_text(json.dumps(result),encoding='utf-8')
    def wait(self,timeout=None):
        return self.returncode


class CompilerProtocolTests(unittest.TestCase):
    def test_probe_binds_typed_inputs_and_in_out_parameters(self):
        pou = source_sections(IN_OUT_SOURCE)
        self.assertEqual(pou['call_parameters'],[
            {'name':'Enable','type':'BOOL','scope':'VAR_INPUT'},
            {'name':'Value','type':'INT','scope':'VAR_IN_OUT'}])
        declaration,implementation = probe_source([pou])
        self.assertIn('PLCFORGE_arg_0_0 : BOOL;',declaration)
        self.assertIn('PLCFORGE_arg_0_1 : INT;',declaration)
        self.assertIn('PLCFORGE_instance_0 : ReviewInOut;',declaration)
        self.assertEqual(implementation,
                         'PLCFORGE_instance_0(Enable := PLCFORGE_arg_0_0, Value := PLCFORGE_arg_0_1);')

    def test_bridge_and_candidate_diagnostics_are_distinct(self):
        self.assertEqual(diagnostic_origin('PLCFORGE_CompileProbe'),'bridge_wrapper')
        self.assertEqual(diagnostic_origin('ReviewInOut'),'candidate')
        self.assertEqual(diagnostic_origin(None),'toolchain')

    def test_raw_profile_value_is_quoted_without_quoting_option_prefix(self):
        argv,command = codesys_command({'executable':'CODESYS.exe','profile':'CODESYS V3.5 SP22 Patch 4'},Path('build.py'))
        self.assertIn(' --profile="CODESYS V3.5 SP22 Patch 4"',command)
        self.assertNotIn(' "--profile=',command)
    def setUp(self):
        parent = ROOT/'.plcforge'/'test-temp'
        parent.mkdir(parents=True,exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.tmp.name)
        # Dummy files are paths only; Popen is replaced by a protocol fixture.
        (self.root/'fixture.exe').write_bytes(b'protocol test only')
        (self.root/'template.project').write_bytes(b'protocol test only')
        self.compiler = CodesysCompiler({'executable':str(self.root/'fixture.exe'),
            'template_project':str(self.root/'template.project'),'profile':'fixture','application':'Application',
            'timeout_seconds':1,'generate_code':True})
    def tearDown(self):
        self.tmp.cleanup()
    def check(self,mode):
        FakeProcess.mode = mode
        with patch('plcforge.compiler.subprocess.Popen',FakeProcess),patch.dict(os.environ,{'MODELSCOPE_API_KEY':'fixture-key-1234'}):
            return self.compiler.compile({'src/PLC_PRG.st':SOURCE},self.root/'builds')
    def test_protocol_accepts_complete_matching_receipt(self):
        self.assertEqual(self.check('valid')['status'],'passed')
    def test_exit_zero_without_receipt_is_not_success(self):
        self.assertEqual(self.check('missing')['status'],'infrastructure_error')
    def test_stale_receipt_is_rejected(self):
        self.assertEqual(self.check('stale')['status'],'infrastructure_error')
    def test_build_must_have_executed(self):
        self.assertEqual(self.check('not_built')['status'],'infrastructure_error')
    def test_error_count_must_be_zero(self):
        self.assertEqual(self.check('errors')['status'],'infrastructure_error')
    def test_code_generation_must_have_executed(self):
        self.assertEqual(self.check('not_generated')['status'],'infrastructure_error')
    def test_toolchain_fingerprint_changes_with_template(self):
        before = self.compiler.fingerprint()
        (self.root/'template.project').write_bytes(b'updated fixture template')
        self.assertNotEqual(before,self.compiler.fingerprint())
    def test_toolchain_fingerprint_changes_with_configuration(self):
        before = self.compiler.fingerprint()
        self.compiler.config['profile'] = 'another installed profile'
        self.assertNotEqual(before,self.compiler.fingerprint())
    def test_verified_receipt_is_invalidated_by_a_changed_template(self):
        receipt=self.root/'verified.json'
        self.compiler.config['verification_receipt']=str(receipt)
        receipt.write_text(json.dumps({'status':'passed','compiler_fingerprint':self.compiler.fingerprint()}),encoding='utf-8')
        self.assertTrue(self.compiler.doctor()['real_compile_verified'])
        (self.root/'template.project').write_bytes(b'changed since verification')
        self.assertFalse(self.compiler.doctor()['real_compile_verified'])
