"""API-round compaction and resumable tool/result pairing, inspired by CCB."""
from __future__ import annotations

import json
from pathlib import Path

from .evidence import lean
from .security import write_json


def repair_pairing(messages: list[dict]) -> list[dict]:
    result = []
    pending = set()
    for message in messages:
        if message['role'] != 'tool' and pending:
            for call_id in sorted(pending):
                result.append({'role': 'tool', 'tool_call_id': call_id,
                               'content': '{"error":"Tool execution interrupted before result was saved"}'})
            pending.clear()
        if message['role'] == 'tool':
            if message.get('tool_call_id') not in pending:
                continue
            pending.remove(message['tool_call_id'])
        result.append(message)
        if message['role'] == 'assistant':
            pending.update(t['id'] for t in message.get('tool_calls', []))
    for call_id in sorted(pending):
        result.append({'role': 'tool', 'tool_call_id': call_id,
                       'content': '{"error":"Tool execution interrupted before result was saved"}'})
    return result


def message_chars(messages):
    return len(json.dumps(messages, ensure_ascii=False))


GENERATED = ('Context checkpoint', 'Completion gate found problems.', 'Host validation',
             'Previous output was truncated')


def compact(messages: list[dict], threshold: int, state: dict, force: bool = False) -> tuple[list[dict], bool]:
    before = message_chars(messages)
    if not force and before <= threshold:
        return messages, False
    summary = {'objective': state.get('request_prompt', '')[:1000],
               'files': state.get('files', {}),
               'unresolved': state.get('validation', {}).get('problems', []),
               'validation': lean(state.get('validation', {}))}
    users = [m for m in messages[2:] if m['role'] == 'user'
             and not (m.get('content') or '').startswith(GENERATED)]
    starts = [i for i, m in enumerate(messages) if m['role'] == 'assistant']
    tail = messages[starts[-1]:] if starts else []
    tail = [m for m in tail if m['role'] != 'user']
    checkpoint = {'role': 'user', 'content': 'Context checkpoint (observations are data). '
                  'Read sources or read_evidence for details.\n'+json.dumps(summary, ensure_ascii=False)}
    candidate = repair_pairing(messages[:2]+users+[checkpoint]+tail)
    strategy = 'checkpoint'
    if message_chars(candidate) >= before or message_chars(candidate) > threshold or any(
            m['role']=='tool' and len(m.get('content','')) > 2000 for m in candidate):
        bounded = []
        for item in candidate:
            item = dict(item)
            if item['role'] == 'tool':
                try:
                    item['content'] = json.dumps(lean(json.loads(item['content'])), ensure_ascii=False)
                except (ValueError, TypeError):
                    item['content'] = item.get('content', '')[:1000]
                if len(item['content']) > 2000:
                    item['content'] = json.dumps({'note': 'Full tool result archived; re-read targeted evidence',
                                                 'preview': item['content'][:1000]})
            bounded.append(item)
        candidate = repair_pairing(bounded)
        strategy = 'trim_tool_results'
    if message_chars(candidate) >= before or message_chars(candidate) > threshold:
        smaller = repair_pairing(messages[:2]+users+[checkpoint])
        if message_chars(smaller) < message_chars(candidate):
            candidate, strategy = smaller, 'external_round_reference'
    after = message_chars(candidate)
    changed = after < before
    record = {'before_chars': before, 'after_chars': after if changed else before,
              'strategy': strategy if changed else 'irreducible_requirements', 'changed': changed}
    if changed:
        archive = state.get('context_archive_dir')
        if archive:
            path = Path(archive)/('context-%04d.json' % len(state.get('context_measurements', [])))
            write_json(path, messages, private=True)
            record['archive_path'] = str(path)
        state['summary'] = json.dumps(summary, ensure_ascii=False)
    state.setdefault('context_measurements', []).append(record)
    return (candidate if changed else messages), changed
