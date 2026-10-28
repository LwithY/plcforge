"""Keep complete evidence on disk and bounded, actionable feedback on the wire."""
from __future__ import annotations

import json
from pathlib import Path

from .security import digest, write_json


def canonical_hash(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')))


def lean(value, depth=0):
    if depth > 6:
        return '[details in evidence file]'
    if isinstance(value, dict):
        return {k: lean(v, depth+1) for k, v in value.items()
                if k not in ('trace', 'cases', 'observations', 'previous_summary', 'raw_output')}
    if isinstance(value, list):
        return [lean(v, depth+1) for v in value[:6]]
    if isinstance(value, str) and len(value) > 700:
        return value[:700]+' [see evidence]'
    return value


def save_report(root: Path, report: dict) -> str:
    identifier = canonical_hash(report)
    write_json(root/'.plcforge/reports'/(identifier+'.json'), report)
    return identifier


def test_feedback(report, cases):
    result = {k: report[k] for k in ('status', 'engine', 'engine_version', 'passed', 'total',
              'source_hashes', 'suite_sha256', 'scan_config', 'test_origin', 'report_id', 'report_path')}
    result['is_plc_runtime'] = False
    result['ltl'] = lean(report['ltl'])
    result['failed_cases'] = []
    for index, case in enumerate(report.get('cases', [])):
        if case['status'] == 'passed':
            continue
        failure = (case.get('failures') or [{}])[0]
        scan = failure.get('scan', len(case.get('trace', [])))
        scans = cases[index].get('scans', [])
        result['failed_cases'].append({
            'name': case['name'], 'status': case['status'], 'first_divergence_scan': scan,
            'scan_number': scan+1, 'failure': failure,
            'inputs': scans[scan].get('inputs', {}) if scan < len(scans) else {},
            'state_prefix': case.get('trace', [])[max(0, scan-1):scan],
        })
        if len(result['failed_cases']) == 2:
            break
    while len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 2000:
        if result['failed_cases'] and 'state_prefix' in result['failed_cases'][0]:
            for case in result['failed_cases']:
                case.pop('state_prefix', None)
        elif len(result['failed_cases']) > 1:
            result['failed_cases'].pop()
        else:
            result['failed_cases'] = [{'note': 'Use read_evidence for full failure details',
                'first_divergence_scan': next((c['failures'][0].get('scan')
                    for c in report.get('cases', []) if c.get('failures')), None)}]
            result['source_hashes'] = {'combined': canonical_hash(report['source_hashes'])}
            break
    return result
