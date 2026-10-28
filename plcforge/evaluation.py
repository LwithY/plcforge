"""Host-only post-run evaluation. No oracle or result is added to model context."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .compiler import CodesysCompiler
from .evidence import canonical_hash
from .security import Workspace, digest, write_json
from .st import ENGINE_VERSION, run_tests
from .tasks import development_hash
from .tools import ToolRegistry


def evaluate(workspace: Path, task: dict, config, holdout: Path, expected_sha256: str,
             output: Path, *, run_result=None):
    workspace, holdout, output = workspace.resolve(), holdout.resolve(), output.resolve()
    if holdout.is_relative_to(workspace) or output.is_relative_to(workspace):
        raise ValueError('Independent expectations and reports must be outside the model workspace')
    raw = holdout.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != expected_sha256:
        raise ValueError('Independent suite hash mismatch')
    oracle = json.loads(raw.decode('utf-8-sig'))
    if oracle.get('task_id') != task['id'] or not oracle.get('version') or not oracle.get('tests'):
        raise ValueError('Independent suite requires matching task_id, version and tests')
    if oracle.get('contract_sha256') != canonical_hash(task.get('contract', {})):
        raise ValueError('Independent suite contract hash mismatch')
    run_result = run_result or {}
    state = {'validation':dict(run_result.get('validation') or {})}
    registry = ToolRegistry(Workspace(workspace), CodesysCompiler(config.codesys), state, task)
    gate = registry.final_gate()
    sources = registry.sources()
    entry = task.get('entry_file','src/PLC_PRG.st')
    if entry not in sources:
        independent = {'status':'failed','passed':0,'total':len(oracle['tests']), 'reason':'missing entry source'}
    else:
        independent = run_tests(sources[entry], oracle['tests'], oracle.get('scan_ms',20))
    report = {'schema_version':1, 'task_id':task['id'], 'suite_version':oracle['version'],
        'independent_sha256':actual_hash, 'development_sha256':development_hash(task),
        'source_hashes':{p:digest(s) for p,s in sources.items()}, 'contract_sha256':canonical_hash(task.get('contract', {})),
        'framework_stop_reason':run_result.get('stop_reason',run_result.get('status','not_supplied')),
        'artifact_validation':gate['status'], 'requirement_review':gate.get('requirement_review'),
        'development_tests':gate.get('tests'), 'independent_checks':independent, 'real_compile':gate.get('compile'),
        'layers':dict(gate.get('layers', {}), independent_subset_scan=independent['status']),
        'model_visibility':{'development':True, 'independent':False, 'oracle_location':'outside workspace',
                            'independent_results_fed_back':False},
        'engine_version':ENGINE_VERSION, 'report_status':'saved'}
    write_json(output, report)
    return report
