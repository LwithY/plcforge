from __future__ import annotations

import json
from pathlib import Path

from .ltl import parse


def validate_task(task: dict) -> list[str]:
    errors = []
    for field in ('id', 'prompt', 'interface', 'specs', 'tests', 'provenance'):
        if not task.get(field):
            errors.append('Missing ' + field)
    if not isinstance(task.get('tests'), list):
        errors.append('tests must be a list')
    else:
        for test in task['tests']:
            if not test.get('scans') or not any(s.get('expect') for s in test.get('scans', [])):
                errors.append('Test needs scans and expected outputs: ' + test.get('name', '?'))
    for spec in task.get('specs', []):
        try:
            parse(spec['formula'])
        except (ValueError, KeyError) as exc:
            errors.append('Invalid LTL: ' + str(exc))
    interface = task.get('interface', {})
    if not interface.get('inputs') or not interface.get('outputs'):
        errors.append('Interface requires inputs and outputs')
    return errors


def load_task(path: Path) -> dict:
    task = json.loads(path.read_text(encoding='utf-8-sig'))
    errors = validate_task(task)
    if errors:
        raise ValueError('; '.join(errors))
    return task


def task_prompt(task: dict, include_tests: bool = True) -> str:
    payload = {k: task[k] for k in ('id', 'prompt', 'interface', 'specs')}
    payload['scan_semantics'] = task.get('scan_semantics',
        'Sample inputs before each scan. Check outputs after that same scan. Internal state persists across scans; each test starts fresh.')
    if include_tests:
        payload['tests'] = task['tests']
    return json.dumps(payload, ensure_ascii=False, indent=2)
