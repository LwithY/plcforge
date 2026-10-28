from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from plcforge.bootstrap import discover_codesys, setup, trusted_env
from plcforge.config import Config, application_home, load_runtime_config
from plcforge.cli import parser
from plcforge.security import register_secret

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        parent = ROOT/'.plcforge/test-temp'
        parent.mkdir(parents=True,exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.root = Path(self.tmp.name)
        self.home = self.root/'user home'

    def tearDown(self):
        self.tmp.cleanup()

    def test_core_setup_creates_safe_defaults_without_copied_credentials(self):
        with redirect_stdout(io.StringIO()):
            report = setup(self.home,skip_codesys=True)
        self.assertEqual(report['status'],'core_ready')
        settings = json.loads((self.home/'settings.json').read_text(encoding='utf-8'))
        self.assertEqual(settings['api_key'],'')
        self.assertEqual(report['codesys']['status'],'unavailable')
        self.assertTrue((self.home/'plcforge.json').is_file())

    def test_upgrade_preserves_existing_llm_settings(self):
        self.home.mkdir()
        file = self.home/'settings.json'
        custom = '{"model":"keep-my-model","api_key_env":"CUSTOM_KEY"}'
        file.write_text(custom,encoding='utf-8')
        with redirect_stdout(io.StringIO()): setup(self.home,skip_codesys=True)
        self.assertEqual(file.read_text(encoding='utf-8'),custom)

    def test_vendor_discovery_uses_installed_profile_and_standard_template(self):
        base = self.root/'vendor/CODESYS'
        for name in ('Common','Profiles','Templates'):
            (base/name).mkdir(parents=True)
        (base/'Common/CODESYS.exe').write_bytes(b'vendor-fixture')
        (base/'Profiles/CODESYS V3.5 SP22 Patch 4.profile.xml').write_text('<Profile/>')
        (base/'Templates/Standard.project').write_bytes(b'template-fixture')
        found = discover_codesys(self.root/'vendor')
        self.assertEqual(found['profile'],'CODESYS V3.5 SP22 Patch 4')
        self.assertEqual(found['executable'],str((base/'Common/CODESYS.exe').resolve()))

    def test_unavailable_compiler_does_not_prevent_core_installation(self):
        with patch('plcforge.bootstrap.discover_codesys',return_value=None),redirect_stdout(io.StringIO()):
            report = setup(self.home)
        self.assertEqual(report['status'],'core_ready')
        self.assertIn('CODESYS not found',report['note'])

    def test_template_failure_is_reported_without_claiming_compilation_passed(self):
        installed = {'executable':'fake-vendor.exe','profile':'fixture','vendor_template':'missing.project'}
        with patch('plcforge.bootstrap.discover_codesys',return_value=installed), \
                patch('plcforge.bootstrap.prepare_template',side_effect=ValueError('fixture failure')),redirect_stdout(io.StringIO()):
            report = setup(self.home)
        self.assertEqual(report['status'],'needs_codesys_setup')
        self.assertFalse(report['codesys']['real_compile_verified'])

    def test_user_home_override_is_used_for_settings_selection(self):
        self.home.mkdir()
        (self.home/'settings.json').write_text('{"model":"home-model","api_key_env":""}')
        with patch.dict(os.environ,{'PLCFORGE_HOME':str(self.home)}):
            self.assertEqual(application_home(),self.home.resolve())
            config = load_runtime_config(None,self.root,self.root/'workspace')
        self.assertEqual(config.model,'home-model')

    def test_source_checkout_without_machine_config_uses_user_runtime_config(self):
        with patch('plcforge.cli.PROJECT_ROOT',self.root/'clean-source'), \
                patch.dict(os.environ,{'PLCFORGE_HOME':str(self.home)}):
            args = parser().parse_args(['doctor'])
        self.assertEqual(args.config,self.home/'plcforge.json')

    def test_native_compiler_does_not_receive_registered_or_named_credentials(self):
        register_secret('bootstrap-custom-fixture-secret')
        with patch.dict(os.environ,{'ODD_CREDENTIAL_NAME':'bootstrap-custom-fixture-secret',
                                   'VENDOR_API_KEY':'bootstrap-named-fixture-secret','SAFE_SETTING':'keep'}):
            env = trusted_env()
        self.assertNotIn('ODD_CREDENTIAL_NAME',env)
        self.assertNotIn('VENDOR_API_KEY',env)
        self.assertEqual(env['SAFE_SETTING'],'keep')
