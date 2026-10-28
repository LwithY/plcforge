from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from . import __version__
from .agent import Agent
from .compiler import CodesysCompiler
from .config import Config, application_home, load_env, load_runtime_config, settings_template
from .progress import TerminalReporter
from .security import redact, write_json
from .st import lint, run_tests
from .tasks import load_task, task_prompt, validate_task

PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMANDS = {'init','setup','doctor','settings','run','chat','resume','lint','test','compile','dataset','bench'}


def normalize_arguments(argv):
    """Bare plcforge opens chat; keep explicit subcommands and global flags."""
    argv = list(argv)
    prefix = []
    while argv and (argv[0] in ('--config','--settings') or argv[0].startswith(('--config=','--settings='))):
        option = argv.pop(0)
        prefix.append(option)
        if option in ('--config','--settings'):
            if not argv:
                return prefix  # argparse reports the missing value.
            prefix.append(argv.pop(0))
    if argv and (argv[0] in COMMANDS or argv[0] in ('--help','-h','--version')):
        return prefix+argv
    default = 'run' if any(a in ('-p','--prompt','--prompt-file','--task') for a in argv) else 'chat'
    return prefix+[default]+argv


def output(value):
    print(redact(json.dumps(value,ensure_ascii=False,indent=2)))


def console_event(kind, value):
    if kind == 'model_start':
        print('[model] turn %d | %s' % (value['turn'],value['model']), flush=True)
    elif kind == 'tool_result':
        result = value['result']
        print('[tool] %s | %s' % (value['name'], result.get('status') or result.get('error') or 'ok'),flush=True)
    elif kind == 'compaction':
        print('[context] checkpoint saved',flush=True)
    elif kind == 'validation_gate':
        print('[gate] ' + value['status'],flush=True)
    elif kind == 'error':
        print('[error] ' + value['message'],file=sys.stderr,flush=True)
    elif kind == 'model_retry':
        print('[retry] 连接中断或服务暂不可用，%.1f 秒后重试 %d/%d' %
              (value['delay_seconds'],value['next_attempt'],value['max_attempts']),flush=True)


def print_run(result, show_model_summary=False):
    if result['status'] in ('answered','needs_input'):
        if not result.get('final_message_displayed'):
            print('\n'+redact(result.get('final_message') or ''))
        if result.get('transcript_path'):
            print('\n任务交互记录: '+result['transcript_path'])
        return
    print('\nPLCForge: ' + result['status'])
    print('Workspace: ' + result['workspace'])
    print('Session: ' + result['session_id'])
    tests = result.get('validation',{}).get('tests') or {}
    if tests:
        print('Subset scan tests: %s/%s (%s)' % (tests.get('passed'),tests.get('total'),tests.get('status')))
    compile_result = result.get('validation',{}).get('compile') or {}
    print('CODESYS: ' + compile_result.get('status','not_attempted'))
    if tests:
        print('LTL trace monitor: ' + tests.get('ltl',{}).get('status','not_run'))
        print('Test origin: ' + tests.get('test_origin','unknown'))
    for path in result.get('validation',{}).get('sources',{}):
        print('Source: ' + str(Path(result['workspace'])/path))
    if result.get('final_message') and (show_model_summary or not result.get('final_message_displayed')):
        print('\nModel explanation (validation fields above are authoritative):\n'+redact(result['final_message']))
    print('\nEvidence: ' + str(Path(result['workspace'])/'.plcforge'/'sessions'/result['session_id']/'summary.json'))
    if result.get('transcript_path'):
        print('任务交互记录: '+result['transcript_path'])
    if result['status']=='model_error':
        print('会话已保存。处理连接或接口问题后，交互界面可输入 /retry 继续；新终端可用 resume 恢复。')


def parser():
    root = argparse.ArgumentParser(prog='plcforge', description='PLC Structured Text coding agent. Run plcforge without arguments to open chat in the current folder.')
    root.add_argument('--version',action='version',version=__version__)
    default_config = PROJECT_ROOT/'plcforge.json'
    if not default_config.is_file():
        default_config = application_home()/'plcforge.json'
    root.add_argument('--config',type=Path,default=Path(os.environ.get('PLCFORGE_CONFIG',str(default_config))))
    root.add_argument('--settings',type=Path,help='Read a specific LLM settings.json')
    sub = root.add_subparsers(dest='command',required=True)
    sub.add_parser('init',help='Create local configuration without a credential')
    sub.add_parser('doctor',help='Check model configuration and CODESYS prerequisites')
    setup = sub.add_parser('setup',help='Prepare user configuration and an installed CODESYS compiler')
    setup.add_argument('--codesys',type=Path)
    setup.add_argument('--skip-codesys',action='store_true')
    settings = sub.add_parser('settings',help='Show LLM settings, or create workspace settings.json')
    settings.add_argument('action',choices=['show','init'],nargs='?',default='show')
    settings.add_argument('--workspace',type=Path,default=Path.cwd())
    for name in ('run','chat','resume'):
        p = sub.add_parser(name)
        p.add_argument('--workspace',type=Path,default=Path.cwd())
        p.add_argument('--model')
        p.add_argument('--max-turns',type=int)
        p.add_argument('--wall-seconds',type=int)
        p.add_argument('--json',action='store_true',dest='json_output')
        p.add_argument('--show-model-summary',action='store_true',help='Show the model explanation after verified metrics')
        p.add_argument('--mode',choices=['auto','answer','code'],default='auto',help='Auto route requests, answer without edits, or require PLC implementation checks')
        if name != 'chat':
            p.add_argument('-p','--prompt',default='')
            p.add_argument('--prompt-file',type=Path)
        if name == 'run':
            p.add_argument('--task',type=Path)
        if name == 'resume':
            p.add_argument('session_id')
    p = sub.add_parser('lint')
    p.add_argument('source',type=Path)
    p = sub.add_parser('test')
    p.add_argument('source',type=Path)
    p.add_argument('--task',type=Path,required=True)
    p = sub.add_parser('compile')
    p.add_argument('--workspace',type=Path,required=True)
    p = sub.add_parser('dataset')
    p.add_argument('action',choices=['validate'])
    p.add_argument('--directory',type=Path,default=PROJECT_ROOT/'data'/'tasks')
    p = sub.add_parser('bench')
    p.add_argument('action',choices=['plan','run','score','summarize'])
    p.add_argument('--matrix',type=Path,default=PROJECT_ROOT/'experiments'/'matrix.json')
    p.add_argument('--output',type=Path,default=PROJECT_ROOT/'runs'/'benchmark')
    p.add_argument('--framework',default='plcforge')
    p.add_argument('--model-label',default='A')
    p.add_argument('--limit',type=int)
    p.add_argument('--workspace',type=Path)
    p.add_argument('--task',type=Path)
    p.add_argument('--allow-uncompiled',action='store_true')
    for command_parser in sub.choices.values():
        command_parser.add_argument('--settings',type=Path,default=argparse.SUPPRESS,
                                    help='Read a specific LLM settings.json')
    return root


def config_for_args(args):
    config = load_runtime_config(args.config,PROJECT_ROOT,getattr(args,'workspace',None) or Path.cwd(),args.settings)
    if getattr(args,'model',None):
        config.model = args.model
    if getattr(args,'max_turns',None) is not None:
        config.max_turns = args.max_turns
    if getattr(args,'wall_seconds',None) is not None:
        config.wall_seconds = args.wall_seconds
    return config.validate()


def main(argv=None):
    if hasattr(sys.stdout,'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    args = parser().parse_args(normalize_arguments(sys.argv[1:] if argv is None else argv))
    load_env(application_home()/'.env')
    if not os.environ.get('PLCFORGE_CONFIG'):
        load_env(PROJECT_ROOT/'.env')
    try:
        if args.command=='setup':
            from .bootstrap import setup
            report = setup(codesys=args.codesys,skip_codesys=args.skip_codesys)
            return 0 if report['status']!='needs_codesys_setup' else 3
        if args.command=='settings' and args.action=='init':
            target = args.settings.expanduser().resolve() if args.settings else args.workspace.resolve()/'.plcforge/settings.json'
            if target.exists():
                print('Settings already exist; file kept: '+str(target))
                return 0
            config = load_runtime_config(args.config,PROJECT_ROOT,args.workspace)
            write_json(target,settings_template(config))
            print('Created settings: '+str(target))
            return 0
        config = config_for_args(args)
        if args.command=='settings':
            output(config.describe())
            return 0
        if args.command == 'init':
            if args.config.exists():
                print('Configuration already exists: '+str(args.config))
            else:
                write_json(args.config,config.public())
                print('Created '+str(args.config))
            return 0
        if args.command == 'doctor':
            executables = []
            if config.codesys.get('executable') and Path(config.codesys['executable']).is_file():
                executables.append(str(Path(config.codesys['executable']).resolve()))
            if os.name == 'nt':
                for base in (Path(os.environ.get('ProgramFiles','C:/Program Files')),
                             Path(os.environ.get('ProgramFiles(x86)','C:/Program Files (x86)'))):
                    for directory in base.glob('*CODESYS*'):
                        executables.extend(str(p) for p in directory.glob('**/CODESYS.exe'))
            output({'plcforge':__version__, 'python':sys.version.split()[0],
                    'model':config.model, 'base_url':config.base_url,
                    'credential_present':config.has_key(),'settings':config.describe(),
                    'codesys':CodesysCompiler(config.codesys).doctor(),
                    'discovered_codesys_executables':sorted(set(executables)),
                    'network_checked':False})
            return 0
        if args.command in ('run','resume'):
            task = load_task(args.task) if getattr(args,'task',None) else None
            prompt = task_prompt(task) if task else args.prompt
            if args.prompt_file:
                prompt += '\n'+args.prompt_file.read_text(encoding='utf-8-sig')
            if args.command == 'run' and not prompt.strip():
                raise ValueError('Provide -p, --prompt-file or --task')
            reporter = TerminalReporter()
            agent = Agent(config,args.workspace,task,
                          session_id=getattr(args,'session_id',None),
                          emit=None if args.json_output else reporter)
            try:
                result = agent.run(prompt,mode=args.mode)
            finally:
                reporter.close()
            output(result) if args.json_output else print_run(result,args.show_model_summary)
            return 0 if result['status'] in ('answered','needs_input','completed','awaiting_codesys','generated_validation_incomplete') else 2
        if args.command == 'chat':
            print('PLCForge '+__version__+' | '+config.model)
            print('Workspace: '+str(args.workspace.resolve()))
            print('Settings: '+(config._settings_path or 'legacy defaults'))
            print('Commands: /help /status /model [MODEL_ID] /settings [reload] /retry /trace /files /plan /compact /compile /test /exit')
            reporter = TerminalReporter()
            agent = Agent(config,args.workspace,emit=reporter)
            while True:
                try:
                    text = input('\nplc> ').strip()
                except (EOFError,KeyboardInterrupt):
                    reporter.close()
                    print()
                    return 0
                if text in ('/exit','/quit'):
                    reporter.close()
                    return 0
                if text == '/help':
                    print('Describe PLC logic in Chinese or English. Files are saved under the workspace.\n'
                          '/status: session; /files: files; /plan: current plan; /compact: context checkpoint;\n'
                          '/compile: real CODESYS build; /test: completion checks; /model: current model;\n'
                          '/model ID: start a fresh model session; /retry: resume the interrupted request;\n'
                          '/trace: current task JSON; /settings: effective LLM settings; /settings reload: reload and start a new session.\n'
                          'Public progress is streamed; private reasoning is omitted.')
                elif text == '/status':
                    output({k:v for k,v in agent.state.items() if k not in ('messages','task')})
                elif text == '/trace':
                    print(str(agent.transcript.path) if agent.transcript else '当前会话还没有任务记录。')
                elif text == '/files':
                    output(agent.registry.execute('list_files',{}))
                elif text == '/plan':
                    output(agent.state.get('plan',[]))
                elif text == '/compact':
                    from .context import compact
                    agent.state['messages'],changed = compact(agent.state['messages'],0,agent.state,force=True)
                    agent.save()
                    print('Context checkpoint created' if changed else 'Context is already short')
                elif text == '/compile':
                    output(agent.registry.execute('compile_st',{}))
                elif text == '/test':
                    output(agent.registry.final_gate())
                elif text == '/model':
                    print('Current model: '+config.model+'\nEndpoint: '+config.base_url)
                elif text == '/settings':
                    output(config.describe())
                elif text == '/settings reload':
                    try:
                        candidate = config_for_args(args)
                        candidate_agent = Agent(candidate,args.workspace,emit=reporter)
                    except (ValueError,OSError,KeyError) as exc:
                        print('设置加载失败，继续使用原配置: '+redact(str(exc)))
                    else:
                        config,agent = candidate,candidate_agent
                        output(config.describe())
                        print('Settings reloaded. New session: '+agent.session_id)
                elif text == '/retry':
                    if agent.state['status'] in ('model_error','interrupted','max_turns','wall_time_limit','stagnation'):
                        print_run(agent.run('',mode=args.mode),args.show_model_summary)
                    else:
                        print('当前没有需要重试的中断请求。')
                elif text.startswith('/model '):
                    config.model = text.split(maxsplit=1)[1]
                    agent = Agent(config,args.workspace,emit=reporter)
                    print('New session: '+config.model)
                elif text.startswith('/'):
                    print('Unknown command. Use /help.')
                elif text:
                    print_run(agent.run(text,mode=args.mode),args.show_model_summary)
        if args.command == 'lint':
            result = lint(args.source.read_text(encoding='utf-8-sig'))
            output(result)
            return 0 if result['status']=='passed' else 2
        if args.command == 'test':
            from .ltl import monitor
            task = load_task(args.task)
            result = run_tests(args.source.read_text(encoding='utf-8-sig'),task['tests'],task.get('scan_ms',20))
            result['ltl'] = monitor(task['specs'],result)
            output(result)
            return 0 if result['status']=='passed' and result['ltl']['status']!='violated' else 2
        if args.command == 'compile':
            from .security import Workspace
            from .tools import ToolRegistry
            registry = ToolRegistry(Workspace(args.workspace),CodesysCompiler(config.codesys),{})
            result = registry.execute('compile_st',{})
            write_json(args.workspace/'.plcforge'/'last_compile.json',result)
            output(result)
            return 0 if result['status']=='passed' else 3
        if args.command == 'dataset':
            files = sorted(args.directory.glob('*.json'))
            results = []
            for file in files:
                task = json.loads(file.read_text(encoding='utf-8-sig'))
                results.append({'file':file.name, 'errors':validate_task(task),
                                'review_status':task.get('provenance',{}).get('review_status','unknown')})
            ids = [json.loads(f.read_text(encoding='utf-8-sig'))['id'] for f in files]
            valid = bool(files) and len(ids)==len(set(ids)) and not any(r['errors'] for r in results)
            output({'status':'passed' if valid else 'failed', 'count':len(files), 'tasks':results,
                    'public_tasks_received':False, 'human_review_complete':False})
            return 0 if valid else 2
        if args.command == 'bench':
            from .benchmark import dispatch
            return dispatch(args,config)
    except (ValueError,OSError,KeyError,json.JSONDecodeError) as exc:
        print('PLCForge: '+redact(str(exc)),file=sys.stderr)
        return 2
    return 0
