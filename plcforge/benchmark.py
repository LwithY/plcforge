"""Reproducible framework x model evaluations; missing data stays missing."""
from __future__ import annotations

from datetime import datetime, timezone
import csv
import json
import os
from pathlib import Path
import subprocess
import time

from .agent import Agent
from .compiler import CodesysCompiler, terminate_process_tree
from .config import Config
from .ltl import monitor
from .security import Workspace, digest, write_json
from .st import run_tests
from .tasks import load_task, task_prompt
from .tools import ToolRegistry


def score(workspace: Path, task: dict, config: Config, compile_history=None) -> dict:
    registry = ToolRegistry(Workspace(workspace),CodesysCompiler(config.codesys),{},task)
    sources = registry.sources()
    compilation = registry.execute('compile_st',{})
    source = sources.get(task.get('entry_file','src/PLC_PRG.st'))
    if source is None:
        testing = {'status':'failed','passed':0,'total':len(task['tests']), 'cases':[], 'reason':'entry source missing'}
    else:
        testing = run_tests(source,task['tests'],task.get('scan_ms',20))
    monitoring = monitor(task['specs'],testing)
    history = list(compile_history or [])
    history.append(compilation)
    return {'task_id':task['id'], 'task_sha256':digest(json.dumps(task,sort_keys=True)),
            'source_hashes':{p:digest(s) for p,s in sources.items()},
            'compile':compilation, 'compile_history':history,
            'tests':testing, 'ltl':monitoring,
            'formal_verification':{'status':'not_run','engine':None},
            'test_engine':'st-subset-scan-simulator', 'plc_runtime_tests_performed':False}


def plan(matrix: dict, root: Path, config: Config) -> dict:
    directories = [root/entry for entry in matrix['task_directories']]
    tasks = [load_task(file) for d in directories for file in sorted(d.glob('*.json'))]
    cells = []
    for framework in matrix['frameworks']:
        for label,model in matrix['models'].items():
            missing = []
            if not model.get('id'):
                missing.append('model_id')
            if framework['kind'] == 'external' and not framework.get('command'):
                missing.append('external_cli_command')
            if CodesysCompiler(config.codesys).doctor()['status'] == 'unavailable':
                missing.append('codesys')
            cells.append({'framework':framework['name'],'model_label':label,'model':model.get('id'),
                          'tasks':len(tasks),'repetitions':matrix.get('repetitions',1),'missing':missing})
    return {'cells':cells, 'task_count':len(tasks),
            'public_tasks_present':any(t['provenance']['source_type']=='course_public' for t in tasks),
            'dataset_human_review_complete':all(t['provenance'].get('review_status')=='human_reviewed' for t in tasks),
            'measurement_status':'planned; no experiment outcomes implied'}


def summarize(records: list[dict]) -> dict:
    groups = {}
    for record in records:
        key = (record['framework'],record['model_label'],record.get('model',''))
        groups.setdefault(key,[]).append(record)
    rows = []
    for (framework,label,model),group in sorted(groups.items()):
        n = len(group)
        compiled = sum(r['score']['compile']['status']=='passed' for r in group)
        attempted = sum(r['score']['compile']['status'] in ('passed','failed') for r in group)
        first_pass = 0
        for r in group:
            attempts = [c for c in r['score'].get('compile_history',[]) if c.get('status') in ('passed','failed')]
            first_pass += bool(attempts and attempts[0]['status']=='passed')
        passed_cases = sum(r['score']['tests'].get('passed',0) for r in group)
        cases = sum(r['score']['tests'].get('total',0) for r in group)
        tokens_complete = all(r.get('usage') and not r.get('usage_missing') for r in group)
        rows.append({'framework':framework,'model_label':label,'model':model,'tasks':n,
                     'codesys_tasks_attempted':attempted,
                     'codesys_status_counts':{s:sum(r['score']['compile']['status']==s for r in group)
                        for s in sorted(set(r['score']['compile']['status'] for r in group))},
                     'first_compile_pass_rate':first_pass/n if attempted else None,
                     'final_compile_pass_rate':compiled/n if attempted else None,
                     'subset_task_test_pass_rate':sum(r['score']['tests']['status']=='passed' for r in group)/n,
                     'subset_case_test_pass_rate':passed_cases/cases if cases else None,
                     'subset_unsupported_tasks':sum(r['score']['tests']['status']=='unsupported' for r in group),
                     'formal_verification_pass_rate':None,
                     'elapsed_seconds':sum(r.get('elapsed_seconds',0) for r in group),
                     'total_tokens':sum(r['usage'].get('total_tokens',0) for r in group) if tokens_complete else None})
    return {'groups':rows,'notes':['Null means not measured, never zero.',
        'Subset simulation is separate from real PLC runtime tests and formal verification.',
        'No performance superiority claim is supported until all four matched experiment cells are complete.']}


def dispatch(args, config: Config) -> int:
    def output(value):
        print(json.dumps(value,ensure_ascii=False,indent=2))
    if args.action == 'summarize':
        records = [json.loads(p.read_text(encoding='utf-8')) for p in args.output.glob('**/record.json')]
        result = summarize(records)
        write_json(args.output/'summary.json',result)
        if result['groups']:
            with (args.output/'summary.csv').open('w',encoding='utf-8-sig',newline='') as stream:
                writer = csv.DictWriter(stream,fieldnames=list(result['groups'][0]))
                writer.writeheader()
                writer.writerows(result['groups'])
        output(result)
        return 0
    if args.action == 'score':
        if not args.workspace or not args.task:
            raise ValueError('score requires --workspace and --task')
        result = score(args.workspace,load_task(args.task),config)
        write_json(args.workspace/'.plcforge'/'independent_score.json',result)
        output(result)
        return 0
    matrix = json.loads(args.matrix.read_text(encoding='utf-8-sig'))
    root = args.matrix.resolve().parents[1]
    current_plan = plan(matrix,root,config)
    if args.action == 'plan':
        output(current_plan)
        return 0
    framework = next(f for f in matrix['frameworks'] if f['name']==args.framework)
    model = matrix['models'][args.model_label]
    if not model.get('id'):
        raise ValueError('Set the model ID for '+args.model_label+' in the matrix')
    if framework['kind']=='external' and not framework.get('command'):
        raise ValueError('Configure the actual existing framework executable and argv in matrix.json')
    if not args.allow_uncompiled and CodesysCompiler(config.codesys).doctor()['status']=='unavailable':
        raise ValueError('CODESYS unavailable. Install/configure it for course experiments; --allow-uncompiled is only for development runs.')
    tasks = [(file,load_task(file)) for d in matrix['task_directories'] for file in sorted((root/d).glob('*.json'))]
    if args.limit:
        tasks = tasks[:args.limit]
    run_id = datetime.now().strftime('%Y%m%d-%H%M%S')
    for repetition in range(matrix.get('repetitions',1)):
        for file,task in tasks:
            workspace = args.output/run_id/framework['name']/args.model_label/('%s-r%d' % (task['id'],repetition+1))
            workspace.mkdir(parents=True,exist_ok=False)
            # Task prompt bytes are identical for every framework/model.
            prompt = task_prompt(task)
            prompt_file = workspace/'task_prompt.txt'
            prompt_file.write_text(prompt,encoding='utf-8')
            run_config = Config(**config.public())
            run_config.model = model['id']
            run_config.base_url = model.get('base_url',config.base_url)
            run_config.api_key_env = model.get('api_key_env',config.api_key_env)
            for key in ('max_turns','max_output_tokens','total_token_budget','wall_seconds'):
                if key in matrix.get('limits',{}):
                    setattr(run_config,key,matrix['limits'][key])
            started = time.monotonic()
            if framework['kind']=='plcforge':
                result = Agent(run_config,workspace,task).run(prompt)
                generation = {k:result.get(k) for k in ('status','session_id','stop_reason','artifact_status','requirement_review','report_status')}
            else:
                replacements = {'workspace':str(workspace.resolve()),'prompt_file':str(prompt_file.resolve()),'model':model['id']}
                command = [part.format(**replacements) for part in framework['command']]
                # Explicit argv only; no shell, no guessed vendor CLI flags.
                with (workspace/'external_cli.log').open('w',encoding='utf-8') as log:
                    process = subprocess.Popen(command,cwd=workspace,stdout=log,stderr=subprocess.STDOUT,
                                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    try:
                        process.wait(timeout=run_config.wall_seconds)
                        generation = {'status':'exited','exit_code':process.returncode}
                    except subprocess.TimeoutExpired:
                        terminate_process_tree(process)
                        generation = {'status':'timeout'}
                result = {'usage':None,'usage_missing':True}
            evaluation = score(workspace,task,run_config,result.get('compile_history'))
            record = {'framework':framework['name'],'framework_version':framework.get('version','unrecorded'),
                      'model_label':args.model_label,'model':model['id'],'repetition':repetition+1,
                      'base_url':run_config.base_url,'task_file':str(file),'prompt_sha256':digest(prompt),
                      'generation':generation,'score':evaluation,'usage':result.get('usage'),
                      'usage_missing':result.get('usage_missing',False),
                      'budget_policy':result.get('budget_policy'), 'usage_records':result.get('usage_records'),
                      'model_visible_tests':'development only; use evaluate for a separately hashed, external independent suite',
                      'elapsed_seconds':time.monotonic()-started,'limits':matrix.get('limits',{}),
                      'development_run_without_compiler':args.allow_uncompiled,
                      'at':datetime.now(timezone.utc).isoformat()}
            write_json(workspace/'record.json',record)
            print('%s %s %s | CODESYS=%s | subset tests=%s' %
                  (framework['name'],args.model_label,task['id'],evaluation['compile']['status'],evaluation['tests']['status']),flush=True)
    return 0
