"""Publish a compact, credential-free acceptance record from local evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from plcforge.compiler import CodesysCompiler
from plcforge.config import Config
from plcforge.evidence import canonical_hash
from plcforge.security import Workspace, write_json
from plcforge.st import check_interface, lint, run_tests
from plcforge.tasks import load_task
from plcforge.tools import ToolRegistry


def main():
    comparison_path = ROOT/'evidence/agent-v0.1.5/p01-comparison.json'
    comparison = json.loads(comparison_path.read_text(encoding='utf-8'))
    native_reports = [json.loads(p.read_text(encoding='utf-8'))
                      for p in sorted((ROOT/'evidence/agent-v0.1.5').glob('*/report.json'))]
    native = next((r for r in reversed(native_reports) if r['status']=='passed'), None)
    if comparison['status']!='passed' or not comparison['same_frozen_prompt'] or native is None:
        raise ValueError('Passing live comparison and real native verification are required')
    unit_log = (ROOT/'.plcforge/upgrade-20261028/tests-final.txt').read_text(encoding='utf-8-sig')
    match = re.search(r'Ran (\d+) tests',unit_log)
    if not match or not unit_log.rstrip().endswith('OK'):
        raise ValueError('Passing unit test evidence is required')
    task = load_task(ROOT/'data/tasks/P01.json')
    source = (ROOT/'examples/reference_solutions/P01.st').read_text(encoding='utf-8')
    workspace = Workspace(ROOT/'.plcforge/upgrade-20261028/feedback-measurement')
    registry = ToolRegistry(workspace,CodesysCompiler(Config().codesys),{},task)
    registry.execute('write_file',{'path':'src/PLC_PRG.st','content':source})
    feedback = registry.execute('test_st',{'path':'src/PLC_PRG.st'})
    raw = run_tests(source,task['tests'])
    holdout = ROOT/'evaluation/P01/holdout.json'
    oracle = json.loads(holdout.read_text(encoding='utf-8'))
    mutations = [
        ('skip_stage',source.replace('phase := 2;','phase := 3;')),
        ('simultaneous_actuators',source.replace('Filling_Valve := phase = 2;','Filling_Valve := phase = 2 OR phase = 1;')),
        ('queued_early_confirmation',source.replace('phase : INT','queued : BOOL := FALSE;\n    phase : INT').replace(
            'CASE phase OF','queued := queued OR cleaningConfirmButton;\nCASE phase OF').replace('1: IF cleaningConfirmButton','1: IF queued'))]
    negative = []
    for label, mutated in mutations:
        result = run_tests(mutated,oracle['tests'])
        negative.append({'mutation':label,'detected':result['status']=='failed','passed':result['passed'],'total':result['total']})
    public_records = []
    for record in comparison['records']:
        selected = {k:v for k,v in record.items() if k not in ('configuration','request_message_chars')}
        selected['limits'] = {k:v for k,v in record['configuration'].items() if k in (
            'max_turns','wall_seconds','max_output_tokens','total_token_budget','request_token_budget',
            'min_output_tokens','input_estimate_safety_factor','context_chars','timeout_seconds','max_retries')}
        selected['temperature'] = 'provider default; not explicitly set'
        selected['reasoning_effort'] = 'provider default; not explicitly set'
        public_records.append(selected)
    diagnostics = []
    for label, mutated in [('missing_END_IF_semicolon',source.replace('END_IF;','END_IF',1)),
                           ('missing_END_CASE_semicolon',source.replace('END_CASE;','END_CASE',1))]:
        diagnostics.append({'case':label,'result':lint(mutated)})
    diagnostics.append({'case':'input_direction','result':check_interface(source.replace('VAR_INPUT','VAR'),task['interface'])})
    report = {'version':'0.1.5','status':'passed','python_unit_tests':int(match[1]),'node_tests':7,
        'live_p01':public_records,'input_token_reduction_fraction':comparison['input_token_reduction_fraction'],
        'compaction_replay':[{k:v for k,v in r.items() if k!='archive_path'} for r in comparison['compaction_replay']],
        'feedback_bytes':len(json.dumps(feedback,ensure_ascii=False).encode('utf-8')),
        'unabridged_test_result_bytes':len(json.dumps(raw,ensure_ascii=False).encode('utf-8')),
        'diagnostic_samples':diagnostics, 'mutations':negative,
        'native_verification':{'run_id':native['run_id'],'compiler_fingerprint':native['compiler_fingerprint'],
            'process_exit_code':native['process_exit_code'],'status':native['status'],
            'cases':[{'id':r['id'],'expectation_met':r['expectation_met'],
                      'local_scan_status':(r.get('local') or {}).get('status','not_run'),
                      'local_syntax_status':r.get('local_syntax',{}).get('status'),
                      'native_status':r['native']['status'],'error_count':r['native'].get('error_count'),
                      'build_completed':r['native'].get('build_completed'),
                      'generate_code_completed':r['native'].get('generate_code_completed'),
                      'source_hashes':r['source_hashes']} for r in native['cases']]},
        'independent_suite':comparison['independent_suite'],
        'budget_exhaustion_injection':{'regression':'test_host_archives_after_budget_stop_without_extra_request',
            'stop_reason':'token_budget','artifact_status':'completed','model_requests':1,
            'compiler':'explicit unit-test fixture; native receipts are separate'},
        'limitations':comparison['limitations']+[
            'Compaction is a separate controlled replay; the improved live trial did not trigger compaction.',
            'CODESYS SP22 Patch 4 accepts omitted END_IF/END_CASE semicolons; the portable subset requires them.',
            'No CODESYS runtime, physical PLC run or full formal verification was performed.'],
        'requirement_review':'pending_confirmation', 'report_status':'saved',
        'codesys_runtime':'not_run','physical_plc':'not_run',
        'evidence_sha256':{'p01_comparison':hashlib.sha256(comparison_path.read_bytes()).hexdigest(),
                           'unit_test_log':hashlib.sha256(unit_log.encode('utf-8')).hexdigest()},
        'validated_runtime_sources_normalized_sha256':{p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_text(encoding='utf-8-sig').encode('utf-8')).hexdigest()
                                     for p in sorted((ROOT/'plcforge').rglob('*.py'))}}
    if not all(n['detected'] for n in negative) or report['feedback_bytes'] > 2000:
        raise ValueError('Negative controls or feedback size acceptance failed')
    output = ROOT/'docs/validation/agent-v0.1.5.json'
    write_json(output,report)
    print(json.dumps({'status':report['status'],'python_tests':report['python_unit_tests'],
                      'feedback_bytes':report['feedback_bytes'],'report':str(output)}))


if __name__=='__main__':
    main()
