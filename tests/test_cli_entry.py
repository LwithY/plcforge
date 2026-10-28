from contextlib import redirect_stdout
import io
from pathlib import Path
import unittest
from unittest.mock import patch

from plcforge.cli import main, normalize_arguments, parser


class CLIEntryTests(unittest.TestCase):
    def test_no_arguments_enters_chat(self):
        self.assertEqual(normalize_arguments([]), ['chat'])

    def test_implicit_chat_options(self):
        self.assertEqual(normalize_arguments(['--model','example']), ['chat','--model','example'])

    def test_implicit_prompt_enters_run(self):
        self.assertEqual(normalize_arguments(['-p','motor']), ['run','-p','motor'])

    def test_global_config_is_preserved(self):
        self.assertEqual(normalize_arguments(['--config','local.json']), ['--config','local.json','chat'])
        self.assertEqual(normalize_arguments(['--config=local.json','doctor']), ['--config=local.json','doctor'])

    def test_explicit_commands_and_help_unchanged(self):
        for arguments in (['doctor'],['--version'],['--help'],['run','-p','motor']):
            self.assertEqual(normalize_arguments(arguments),arguments)

    def test_default_workspace_is_current_directory(self):
        args = parser().parse_args(normalize_arguments([]))
        self.assertEqual(args.workspace,Path.cwd())

    def test_bare_main_opens_and_exits_without_model_call(self):
        with patch('plcforge.cli.Agent') as constructor, patch('builtins.input',return_value='/exit'), \
                patch('plcforge.cli.load_env'), redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main([]),0)
            self.assertEqual(constructor.call_args.args[1],Path.cwd())
            self.assertIn('Workspace: '+str(Path.cwd().resolve()),output.getvalue())
