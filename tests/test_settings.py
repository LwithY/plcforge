"""Provider settings selection, authenticated requests, reload and credential isolation."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plcforge.agent import Agent
from plcforge.cli import main, normalize_arguments, parser
from plcforge.config import Config, load_runtime_config
from plcforge.progress import PublicTextBuffer
from plcforge.provider import OpenAIProvider
from plcforge.security import Workspace, redact
from test_conversation_transport import response

ROOT = Path(__file__).resolve().parents[1]


class SettingsTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT/'.plcforge/test-temp'
        parent.mkdir(parents=True,exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.tmp.name)
        self.project, self.home, self.workspace = (self.root/x for x in ('project','home','workspace'))
        for directory in (self.project,self.home,self.workspace):
            directory.mkdir()
        self.environment = patch.dict(os.environ,{},clear=False)
        self.environment.start()
        for key in ('PLCFORGE_BASE_URL','PLCFORGE_MODEL','PLCFORGE_API_KEY'):
            os.environ.pop(key,None)

    def tearDown(self):
        self.environment.stop()
        self.tmp.cleanup()

    def write(self, path, **values):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps(values,ensure_ascii=False),encoding='utf-8')
        return path

    def load(self, explicit=None):
        return load_runtime_config(self.project/'plcforge.json',self.project,self.workspace,explicit,user_home=self.home)

    def test_selection_prefers_workspace_then_user_then_installation_and_explicit(self):
        installed = self.write(self.project/'settings.json',model='installed',api_key_env='')
        self.assertEqual(self.load().model,'installed')
        user = self.write(self.home/'.plcforge/settings.json',model='user',api_key_env='')
        self.assertEqual(self.load().model,'user')
        local = self.write(self.workspace/'.plcforge/settings.json',model='local',api_key_env='')
        self.assertEqual(self.load().model,'local')
        self.assertEqual(self.load(installed).model,'installed')
        self.assertEqual(self.load(user).model,'user')
        self.assertEqual(self.load(local)._settings_path,str(local.resolve()))

    def test_settings_keep_codesys_and_runtime_limits_from_legacy_config(self):
        self.write(self.project/'plcforge.json',max_turns=9,codesys={'executable':'vendor-compiler'})
        self.write(self.project/'settings.json',base_url='https://example.invalid/v1/',model='new-model',max_output_tokens=2048)
        config = self.load()
        self.assertEqual(config.codesys['executable'],'vendor-compiler')
        self.assertEqual(config.max_turns,9)
        self.assertEqual(config.max_output_tokens,2048)
        self.assertEqual(config.base_url,'https://example.invalid/v1')

    def test_inline_key_and_model_reach_correct_openai_compatible_request(self):
        key = 'settings-inline-fixture-key-20261028'
        self.write(self.project/'settings.json',base_url='https://custom.invalid/v1/chat/completions',model='vendor/model-v2',api_key=key)
        config = self.load()
        with patch('plcforge.provider.request.urlopen',return_value=response()) as send:
            OpenAIProvider(config).complete([{'role':'user','content':'hello'}],[])
        req = send.call_args.args[0]
        self.assertEqual(req.full_url,'https://custom.invalid/v1/chat/completions')
        self.assertEqual(json.loads(req.data)['model'],'vendor/model-v2')
        self.assertEqual(req.get_header('Authorization'),'Bearer '+key)
        self.assertNotIn(key,json.dumps(config.public()))
        self.assertNotIn(key,json.dumps(config.describe()))
        self.assertNotIn(key,repr(config))
        self.assertEqual(redact(key),'[REDACTED]')

    def test_custom_environment_variable_is_resolved_and_registered_for_redaction(self):
        os.environ['CUSTOM_LLM_CREDENTIAL'] = 'custom-variable-fixture-key-20261028'
        self.write(self.project/'settings.json',model='env-model',api_key_env='CUSTOM_LLM_CREDENTIAL')
        config = self.load()
        self.assertEqual(config.key(),os.environ['CUSTOM_LLM_CREDENTIAL'])
        self.assertEqual(redact(config.key()),'[REDACTED]')

    def test_new_provider_file_does_not_inherit_legacy_or_lower_priority_key(self):
        os.environ['MODELSCOPE_API_KEY'] = 'legacy-provider-fixture-key-20261028'
        self.write(self.project/'plcforge.json',api_key='legacy-inline-fixture-key-20261028')
        self.write(self.project/'settings.json',api_key='lower-priority-fixture-key-20261028')
        self.write(self.workspace/'.plcforge/settings.json',base_url='https://different.invalid/v1',model='different')
        config = self.load()
        self.assertFalse(config.has_key())
        self.assertEqual(config.api_key_env,'PLCFORGE_API_KEY')
        with self.assertRaisesRegex(ValueError,'Missing PLCFORGE_API_KEY'):
            config.key()

    def test_environment_overrides_model_and_url_without_forwarding_another_key(self):
        self.write(self.project/'settings.json',api_key='old-provider-fixture-key-20261028')
        os.environ['PLCFORGE_BASE_URL'] = 'http://127.0.0.1:9000/v1'
        os.environ['PLCFORGE_MODEL'] = 'environment-model'
        config = self.load()
        self.assertEqual(config.base_url,'http://127.0.0.1:9000/v1')
        self.assertEqual(config.model,'environment-model')
        self.assertEqual(config.api_key,'')
        self.assertFalse(config.has_key())

    def test_local_unauthenticated_endpoint_omits_authorization_header(self):
        self.write(self.project/'settings.json',base_url='http://127.0.0.1:11434/v1',model='local-plc',api_key_env='')
        with patch('plcforge.provider.request.urlopen',return_value=response()) as send:
            OpenAIProvider(self.load()).complete([{'role':'user','content':'hello'}],[])
        self.assertIsNone(send.call_args.args[0].get_header('Authorization'))

    def test_invalid_settings_fail_locally_without_echoing_secret_values(self):
        path = self.project/'settings.json'
        for values in ({'model':''},{'base_url':'ftp://example.invalid'}, {'base_url':'https://key:secret@example.invalid'},
                       {'base_url':'https://example.invalid?token=secret'}, {'unknown':'secret'}, {'api_key':42},
                       {'api_key_env':'bad name'},{'max_output_tokens':0},{'max_retries':-1}):
            with self.subTest(values=values):
                self.write(path,**values)
                with self.assertRaises(ValueError): self.load()
        path.write_text('{"api_key": "incomplete-secret',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'line 1') as error:
            self.load()
        self.assertNotIn('incomplete-secret',str(error.exception))
        with self.assertRaisesRegex(ValueError,'does not exist'):
            self.load(self.root/'missing.json')

    def test_bom_file_and_full_completion_url_are_supported(self):
        path = self.project/'settings.json'
        path.write_text(json.dumps({'base_url':'https://example.invalid/v1/chat/completions/','model':'模型A','api_key_env':''}),encoding='utf-8-sig')
        self.assertEqual(self.load().base_url,'https://example.invalid/v1')
        self.assertEqual(self.load().model,'模型A')

    def test_settings_option_works_before_or_after_subcommand(self):
        for argv in (['--settings','x.json'],['--settings=x.json'],['chat','--settings','x.json'],
                     ['--settings','x.json','doctor'],['run','--settings','x.json','-p','hello']):
            with self.subTest(argv=argv):
                args = parser().parse_args(normalize_arguments(argv))
                self.assertEqual(args.settings,Path('x.json'))

    def test_model_tools_cannot_read_settings_files(self):
        workspace = Workspace(self.workspace)
        self.write(self.workspace/'settings.json',api_key='blocked-settings-fixture-key-20261028')
        for path in ('settings.json','sub/Settings.JSON','.plcforge/settings.json'):
            with self.assertRaises(ValueError): workspace.path(path)
        self.assertEqual(workspace.files(),[])

    def test_direct_settings_key_is_redacted_from_task_transcript_and_stream(self):
        key = 'transcript-settings-fixture-key-20261028'
        self.write(self.project/'settings.json',api_key=key)
        config = self.load()
        visible = []
        with patch('plcforge.provider.request.urlopen',return_value=response({'role':'assistant','content':'Echo '+key})):
            result = Agent(config,self.workspace,emit=lambda k,v:visible.append((k,v))).run('解释PLC扫描周期')
        self.assertNotIn(key,Path(result['transcript_path']).read_text(encoding='utf-8'))
        self.assertNotIn(key,json.dumps(visible))
        buffer = PublicTextBuffer()
        output = buffer.feed(key[:13])+buffer.feed(key[13:]+' '+'z'*400)+buffer.feed(final=True)
        self.assertNotIn(key,output)
        self.assertIn('[REDACTED]',output)

    def test_settings_init_creates_safe_template_and_never_overwrites_existing_file(self):
        target = self.workspace/'custom settings.json'
        argv = ['settings','init','--workspace',str(self.workspace),'--settings',str(target)]
        with patch('plcforge.cli.load_env'),redirect_stdout(io.StringIO()):
            self.assertEqual(main(argv),0)
        values = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(values['api_key'],'')
        target.write_text('{"model":"keep-my-file"}',encoding='utf-8')
        with patch('plcforge.cli.load_env'),redirect_stdout(io.StringIO()):
            self.assertEqual(main(argv),0)
        self.assertEqual(target.read_text(encoding='utf-8'),'{"model":"keep-my-file"}')

    def test_chat_reload_switches_provider_and_creates_new_session(self):
        path = self.write(self.workspace/'.plcforge/settings.json',base_url='https://first.invalid/v1',model='first',api_key='first-reload-fixture-key-20261028')
        def inputs():
            yield '/settings'
            self.write(path,base_url='https://second.invalid/v1',model='second',api_key='second-reload-fixture-key-20261028')
            yield '/settings reload'
            yield '解释PLC扫描周期'
            yield '/exit'
        with patch('plcforge.cli.load_env'),patch('builtins.input',side_effect=inputs()), \
                patch('plcforge.cli.Agent',wraps=Agent) as constructors, \
                patch('plcforge.provider.request.urlopen',return_value=response()) as send, \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['chat','--workspace',str(self.workspace)]),0)
        self.assertEqual(constructors.call_count,2)
        self.assertEqual(send.call_args.args[0].full_url,'https://second.invalid/v1/chat/completions')
        self.assertEqual(json.loads(send.call_args.args[0].data)['model'],'second')
        self.assertEqual(send.call_args.args[0].get_header('Authorization'),'Bearer second-reload-fixture-key-20261028')
        self.assertNotIn('second-reload-fixture-key-20261028',output.getvalue())

    def test_bad_reload_keeps_previous_config_and_session(self):
        path = self.write(self.workspace/'.plcforge/settings.json',base_url='https://kept.invalid/v1',model='kept',api_key_env='')
        def inputs():
            path.write_text('{broken json',encoding='utf-8')
            yield '/settings reload'
            yield '/model'
            yield '/exit'
        with patch('plcforge.cli.load_env'),patch('builtins.input',side_effect=inputs()), \
                patch('plcforge.cli.Agent',wraps=Agent) as constructors, redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['chat','--workspace',str(self.workspace)]),0)
        self.assertEqual(constructors.call_count,1)
        self.assertIn('继续使用原配置',output.getvalue())
        self.assertIn('Current model: kept',output.getvalue())


if __name__=='__main__':
    unittest.main()
