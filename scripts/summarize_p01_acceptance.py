"""Evaluate completed/stopped runs locally and preserve an auditable comparison."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from plcforge.config import Config
from plcforge.context import compact
from plcforge.evaluation import evaluate
from plcforge.security import write_json
from plcforge.st import run_tests
from plcforge.tasks import load_task
from plcforge.tools import ToolRegistry
from plcforge.compiler import CodesysCompiler
from plcforge.security import Workspace


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline-run',required=True)
    parser.add_argument('--improved-run',required=True)
    args = parser.parse_args()
    config = Config.load(ROOT/'plcforge.json')
    task = load_task(ROOT/'data/tasks/P01.json')
    holdout = ROOT/'evaluation/P01/holdout.json'
    oracle = json.loads((holdout.parent/'manifest.json').read_text(encoding='utf-8'))
    output = ROOT/'evidence/agent-v0.1.5/p01-comparison.json'
    records = []
    for identifier in (args.baseline_run, args.improved_run):
        run = ROOT/'.plcforge/acceptance'/identifier
        result = json.loads((run/'agent_result.json').read_text(encoding='utf-8'))
        manifest = json.loads((run/'manifest.json').read_text(encoding='utf-8'))
        independent = evaluate(run/'workspace',task,config,holdout,oracle['sha256'],run/('independent-'+oracle['sha256'][:12]+'.json'),run_result=result)
        transcript = json.loads(Path(result['transcript_path']).read_text(encoding='utf-8'))
        source_writes = [t for r in transcript['rounds'] for t in r.get('tools', [])
                         if t['name'] in ('write_file','edit_file') and (t.get('arguments') or {}).get('path','').endswith('.st')]
        records.append({'run_id':identifier,'implementation':manifest['implementation'], 'version':manifest['version'],
            'model':manifest['model'],'prompt_sha256':manifest['prompt_sha256'],
            'task_file_sha256':manifest['task_file_sha256'],
            'configuration':manifest['configuration'], 'framework_status':result['status'],
            'stop_reason':result.get('stop_reason', result['status']),
            'artifact_status':result.get('artifact_status', result.get('validation',{}).get('status')),
            'model_calls':result['model_calls'],'usage':result['usage'],'usage_missing':result.get('usage_missing',False),
            'elapsed_seconds':result.get('elapsed_seconds'), 'source_writes':len(source_writes),
            'source_modifications':max(0,len(source_writes)-1),'compile_count':len(result.get('compile_history',[])),
            'development':{k:independent['development_tests'].get(k) for k in ('status','passed','total','suite_sha256')},
            'independent':{k:independent['independent_checks'].get(k) for k in ('status','passed','total')},
            'compile':{k:independent['real_compile'].get(k) for k in ('status','error_count','build_completed','generate_code_completed','source_hashes')},
            'requirement_review':independent['requirement_review'],
            'request_message_chars':[len(json.dumps(r['request']['body']['messages'],ensure_ascii=False)) for r in transcript['rounds']],
            'source_hashes':independent['source_hashes'], 'cash_cost':None,
            'cash_cost_status':'No price schedule or account bill supplied; no exact cash fee claimed'})
    # Controlled replay of the observed large-result shape: measured, no model call.
    source = (ROOT/'examples/reference_solutions/P01.st').read_text(encoding='utf-8')
    raw = run_tests(source, task['tests'])
    messages = [{'role':'system','content':'ST agent'},{'role':'user','content':'P01 frozen provisional contract'}]
    for i in range(6):
        messages += [{'role':'assistant','content':None,'tool_calls':[{'id':str(i),'type':'function','function':{'name':'test_st','arguments':'{}'}}]},
                     {'role':'tool','tool_call_id':str(i),'content':json.dumps(raw,ensure_ascii=False)}]
    messages.append({'role':'user','content':'Keep the provisional level-trigger convention; review remains pending.'})
    state = {'validation':{'tests':raw}, 'context_archive_dir':str(ROOT/'.plcforge/upgrade-20261028/context-replay')}
    _, changed = compact(messages,80000,state,force=True)
    if not changed:
        raise ValueError('Large-result compaction did not shrink')
    old, new = records
    frozen_payload = {k:task[k] for k in ('id','prompt','interface','specs','tests','contract','scan_semantics','development_suite_sha256')}
    frozen_payload['model_visibility'] = 'development tests and provisional contract only; independent checks withheld'
    from plcforge.security import digest
    if digest(json.dumps(frozen_payload,ensure_ascii=False,indent=2)) != old['prompt_sha256']:
        raise ValueError('Current P01 task content differs from the frozen live input')
    failed_attempts = []
    for file in (ROOT/'.plcforge/acceptance').glob('*/manifest.json'):
        item = json.loads(file.read_text(encoding='utf-8'))
        if item['status'] != 'completed':
            failed_attempts.append({k:item.get(k) for k in ('run_id','implementation','status')})
    report = {'at':datetime.now(timezone.utc).isoformat(),'status':'passed' if all(
        r['development']['status']=='passed' and r['independent']['status']=='passed' and r['compile']['status']=='passed' for r in records) else 'failed',
        'records':records, 'same_frozen_prompt':old['prompt_sha256']==new['prompt_sha256'],
        'same_frozen_task':old['task_file_sha256']==new['task_file_sha256'],
        'input_token_reduction_fraction':1-new['usage']['prompt_tokens']/old['usage']['prompt_tokens'],
        'compaction_replay':state['context_measurements'], 'failed_infrastructure_attempts':failed_attempts,
        'independent_suite':oracle, 'runtime_tests':'not_run','physical_plc':'not_run',
        'limitations':['One live run per implementation with an updateable model alias; no general performance ranking.',
            'Published JSON uses fixed LF line endings. Frozen per-run input bytes are preserved locally; model-visible task content is checked unchanged.',
            'Original collaborator evidence is unavailable and was not modified; both local suites are new reconstructions.',
            'Baseline system prompt/tool definitions differ because they are implementation changes. Task input is frozen.',
            'Independent expectations were outside both model workspaces and never fed back for repair.']}
    write_json(output,report)
    print(json.dumps({'status':report['status'],'same_frozen_prompt':report['same_frozen_prompt'],
        'input_token_reduction_fraction':report['input_token_reduction_fraction'], 'report':str(output),
        'compaction':report['compaction_replay']},ensure_ascii=False))
    return 0 if report['status']=='passed' else 2


if __name__=='__main__':
    raise SystemExit(main())
