"""New, isolated real CODESYS receipts for P01, diagnostics and extension types."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from plcforge.compiler import CodesysCompiler, codesys_command, source_sections, terminate_process_tree
from plcforge.config import Config
from plcforge.security import digest, write_json
from plcforge.st import lint, run_tests


def main():
    run_id = datetime.now().strftime('%Y%m%d-%H%M%S-')+uuid.uuid4().hex[:6]
    run = ROOT/'evidence/agent-v0.1.5'/run_id
    run.mkdir(parents=True,exist_ok=False)
    config = Config.load(ROOT/'plcforge.json')
    compiler = CodesysCompiler(config.codesys)
    if compiler.doctor()['status']=='unavailable':
        raise ValueError('Real CODESYS is required for this acceptance run')
    source = (ROOT/'examples/reference_solutions/P01.st').read_text(encoding='utf-8')
    cases = [{'id':'P01','sources':{'src/PLC_PRG.st':source},'native_should_pass':True}]
    cases += json.loads((ROOT/'evaluation/extensions.json').read_text(encoding='utf-8'))['cases']
    cases += [
        {'id':'invalid_unknown_symbol','sources':{'src/PLC_PRG.st':source.replace('phase := 1;', 'phase := UnknownPhase;')},'native_should_pass':False},
        # SP22 accepts these omitted terminator semicolons. The local portable
        # subset deliberately requires them and provides a precise diagnostic.
        {'id':'missing_end_if_semicolon','sources':{'src/PLC_PRG.st':source.replace('END_IF;','END_IF',1)},'native_should_pass':True, 'local_lint_status':'failed'},
        {'id':'missing_end_case_semicolon','sources':{'src/PLC_PRG.st':source.replace('END_CASE;','END_CASE',1)},'native_should_pass':True, 'local_lint_status':'failed'}]
    jobs = []
    for case in cases:
        directory = run/case['id']
        directory.mkdir()
        project = directory/'build.project'
        shutil.copy2(config.codesys['template_project'],project)
        job = {'job_id':uuid.uuid4().hex,'case_name':case['id'],'project':str(project.resolve()),
               'result':str((directory/'result.json').resolve()),'application':config.codesys['application'],
               'source_hashes':{p:digest(s) for p,s in case['sources'].items()},
               'pous':[dict(source_sections(s),path=p) for p,s in case['sources'].items()], 'generate_code':True}
        write_json(directory/'job.json',job)
        jobs.append(job)
    manifest = run/'batch.json'
    write_json(manifest, {'jobs':jobs,'bridge_directory':str(ROOT/'plcforge/bridge'), 'progress':str(run/'progress.json')})
    argv, command = codesys_command(config.codesys,ROOT/'scripts/codesys_batch_build.py')
    env = {k:v for k,v in os.environ.items() if not any(word in k.upper() for word in ('API_KEY','TOKEN','SECRET','PASSWORD'))}
    env['PLCFORGE_BATCH_MANIFEST'] = str(manifest.resolve())
    with (run/'process.log').open('w',encoding='utf-8') as stream:
        process = subprocess.Popen(command if os.name=='nt' else argv,env=env,cwd=run,stdout=stream,stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            process.wait(timeout=600)
        except (subprocess.TimeoutExpired,KeyboardInterrupt):
            terminate_process_tree(process)
            raise
    records = []
    for case, job in zip(cases,jobs):
        path = Path(job['result'])
        result = json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {'status':'infrastructure_error'}
        identified = result.get('job_id')==job['job_id'] and result.get('source_hashes')==job['source_hashes']
        passed = identified and result.get('status')=='passed' and result.get('build_completed') is True and result.get('generate_code_completed') is True and result.get('error_count')==0 and process.returncode==0
        rejected = identified and result.get('status')=='failed' and result.get('build_completed') is True and result.get('error_count',0)>0
        local = run_tests(case['sources']['src/PLC_PRG.st'],case['tests']) if case.get('tests') else None
        syntax = lint(case['sources']['src/PLC_PRG.st'])
        expectation_met = (passed if case['native_should_pass'] else rejected) and (not local or local['status']==case['local_status']) and (not case.get('local_lint_status') or syntax['status']==case['local_lint_status'])
        records.append({'id':case['id'],'expectation_met':expectation_met, 'native_should_pass':case['native_should_pass'],
                        'local':local, 'local_syntax':syntax, 'native':result,'source_hashes':job['source_hashes']})
    report = {'run_id':run_id,'at':datetime.now(timezone.utc).isoformat(), 'status':'passed' if all(r['expectation_met'] for r in records) else 'failed',
        'compiler_fingerprint':compiler.fingerprint(),'process_exit_code':process.returncode,'cases':records,
        'verification':'real offline CODESYS compile and code generation', 'codesys_runtime':'not_run','physical_plc':'not_run'}
    write_json(run/'report.json',report)
    print(json.dumps({'status':report['status'],'report':str(run/'report.json'),
                      'cases':[{'id':r['id'],'expected':r['expectation_met'],'native':r['native']['status']} for r in records]}),flush=True)
    return 0 if report['status']=='passed' else 2


if __name__=='__main__':
    raise SystemExit(main())
