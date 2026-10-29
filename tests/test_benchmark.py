"""Batch planning, credential isolation and experiment identity regressions."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from plcforge.benchmark import dispatch, plan, run_config_for_model
from plcforge.cli import main
from plcforge.config import Config
from plcforge.tasks import validate_task


ROOT = Path(__file__).resolve().parents[1]


def matrix():
    return json.loads((ROOT/'experiments'/'matrix.json').read_text(encoding='utf-8'))


class DummyAgent:
    """No-network batch fixture; scoring still runs for every published task."""
    def __init__(self, config, workspace, task):
        self.config = config
    def run(self, prompt):
        return {'status':'fixture','session_id':'fixture','stop_reason':'fixture',
                'artifact_status':'not_created','requirement_review':None,
                'report_status':'not_created','compile_history':[],
                'usage':None,'usage_missing':True,'budget_policy':None,'usage_records':[]}


class BenchmarkTests(unittest.TestCase):
    def test_plan_preflights_every_published_task_without_a_model_key(self):
        result = plan(matrix(),ROOT,Config())
        self.assertEqual(result['task_count'],13)
        self.assertEqual(len(result['tasks']),13)
        self.assertEqual({task['id'] for task in result['tasks']},
                         {'P01'} | {'S%02d' % number for number in range(1,13)})
        p01 = next(task for task in result['tasks'] if task['id']=='P01')
        self.assertEqual(p01['source_type'],'course_public')
        self.assertTrue(result['public_tasks_present'])
        plcforge = next(item for item in result['frameworks'] if item['kind']=='plcforge')
        self.assertEqual(plcforge['version'],'0.1.6')
        self.assertRegex(plcforge['source_sha256'],r'^[0-9a-f]{64}$')

    def test_dataset_validation_reports_the_public_task_from_provenance(self):
        with patch('plcforge.cli.load_env'),redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['dataset','validate','--directory',str(ROOT/'data'/'tasks')]),0)
        result = json.loads(output.getvalue())
        self.assertTrue(result['public_tasks_received'])
        self.assertFalse(result['human_review_complete'])

    def test_run_preflight_traverses_every_task_without_contacting_a_model(self):
        parent = ROOT/'.plcforge'/'test-temp'
        parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as temporary:
            output = Path(temporary)/'runs'
            args = SimpleNamespace(action='run',matrix=ROOT/'experiments'/'matrix.json',
                output=output,framework='plcforge',model_label='A',limit=None,
                workspace=None,task=None,allow_uncompiled=True)
            with patch('plcforge.benchmark.Agent',DummyAgent),redirect_stdout(io.StringIO()):
                self.assertEqual(dispatch(args,Config()),0)
            records = list(output.glob('**/record.json'))
            self.assertEqual(len(records),13)
            self.assertEqual({json.loads(path.read_text(encoding='utf-8'))['score']['task_id']
                              for path in records},
                             {'P01'} | {'S%02d' % number for number in range(1,13)})

    def test_matrix_version_mismatch_is_rejected_before_a_run(self):
        stale = deepcopy(matrix())
        stale['frameworks'][0]['version'] = '0.1.5'
        with self.assertRaisesRegex(ValueError,'runtime version is 0.1.6'):
            plan(stale,ROOT,Config())

    def test_inline_settings_key_survives_only_in_the_runtime_copy(self):
        key = 'benchmark-inline-fixture-key-20261029'
        copied = run_config_for_model(Config(api_key=key),matrix()['models']['A'])
        self.assertEqual(copied.key(),key)
        self.assertEqual(copied.describe()['credential_source'],'settings api_key')
        self.assertNotIn(key,json.dumps(copied.public()))

    def test_model_environment_key_is_bound_without_entering_public_metadata(self):
        key = 'benchmark-environment-fixture-key-20261029'
        model = {'id':'fixture/model','base_url':'https://api-inference.modelscope.cn/v1',
                 'api_key_env':'BENCHMARK_MODEL_KEY'}
        with patch.dict(os.environ,{'BENCHMARK_MODEL_KEY':key}):
            copied = run_config_for_model(Config(api_key_env=''),model)
            self.assertEqual(copied.key(),key)
            self.assertEqual(copied.describe()['credential_source'],'BENCHMARK_MODEL_KEY')
            self.assertNotIn(key,json.dumps(copied.public()))

    def test_inline_key_is_not_forwarded_when_matrix_changes_endpoint(self):
        model = {'id':'fixture/model','base_url':'https://different.invalid/v1','api_key_env':''}
        copied = run_config_for_model(Config(api_key='endpoint-bound-fixture-key'),model)
        self.assertEqual(copied.api_key,'')
        self.assertEqual(copied.api_key_env,'')

    def test_provenance_error_names_task_and_missing_field(self):
        task = {'id':'P99','prompt':'x','interface':{'inputs':{'x':'BOOL'},'outputs':{'y':'BOOL'}},
                'specs':[{'formula':'G y'}],
                'tests':[{'name':'x','scans':[{'expect':{'y':False}}]}],
                'provenance':{'review_status':'pending_confirmation'}}
        self.assertIn('Task P99 provenance missing required field: source_type',validate_task(task))


if __name__ == '__main__':
    unittest.main()
