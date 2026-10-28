"""Per-task, numbered API exchanges. Export public messages, never hidden reasoning."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from .security import redact, write_json

PRIVATE_FIELDS = {'reasoning_content', 'reasoning_details', 'thinking', 'reasoning'}


def now():
    return datetime.now(timezone.utc).isoformat()


def public_copy(value):
    """Keep protocol structure; remove private reasoning and configured credentials."""
    def clean(item):
        if isinstance(item, dict):
            return {k:clean(v) for k,v in item.items() if k not in PRIVATE_FIELDS}
        if isinstance(item, list):
            return [clean(v) for v in item]
        return item
    return json.loads(redact(json.dumps(clean(value), ensure_ascii=False)))


def formatted_messages(messages):
    """Parsed companions help inspection without changing wire-protocol strings."""
    result = []
    for index,message in enumerate(messages):
        item = {'message_index':index+1, **public_copy(message)}
        if message.get('role') == 'tool':
            try:
                item['formatted_content'] = public_copy(json.loads(message['content']))
            except (ValueError,TypeError):
                pass
        for call in item.get('tool_calls', []):
            try:
                call['function']['formatted_arguments'] = public_copy(json.loads(call['function']['arguments']))
            except (ValueError,TypeError,KeyError):
                pass
        result.append(item)
    return result


class TaskTranscript:
    def __init__(self, directory: Path, state: dict, prompt: str, mode: str, resume=False):
        # Only numeric host-generated filenames can be selected from saved state.
        sequence = state.get('transcript_sequence', 0)
        if type(sequence) is not int or sequence < 0:
            sequence = 0
        sequence = sequence if resume and sequence else sequence+1
        state['transcript_sequence'] = sequence
        self.path = directory/'tasks'/('task-%04d.json' % sequence)
        if resume and self.path.is_file():
            self.data = json.loads(self.path.read_text(encoding='utf-8'))
            # A process can die between the last checkpoint and finalization.
            self.close_pending('interrupted', 'Previous process stopped before completing this round')
            self.data['resumptions'] = self.data.get('resumptions', 0)+1
        else:
            self.data = {'schema_version':1, 'session_id':state['session_id'],
                         'task_number':sequence, 'started_at':now(), 'workspace':str(directory.parents[2]),
                         'prompt':public_copy(prompt), 'initial_mode':mode, 'mode':mode, 'status':'running',
                         'model':state['model'], 'base_url':state['base_url'],
                         'export_policy':{'credentials':'redacted', 'hidden_reasoning':'omitted',
                                          'response_format':'assembled chat message, not raw SSE'},
                         'rounds':[], 'local_responses':[], 'stages':[]}
        self.data.update(status='running', updated_at=now())
        self.current = None
        self.save()

    def save(self):
        self.data['updated_at'] = now()
        write_json(self.path, self.data)

    def start_round(self, payload, url, loop_turn, session_round):
        self.current = {'round':len(self.data['rounds'])+1, 'loop_turn':loop_turn,
                        'session_model_round':session_round, 'started_at':now(), 'status':'pending',
                        'request':{'method':'POST', 'url':url, 'body':public_copy(payload)},
                        'formatted_messages':formatted_messages(payload['messages']),
                        'attempts':[], 'response':None, 'tools':[]}
        self.data['rounds'].append(self.current)
        self.save()
        return self.current['round']

    def attempt(self, details):
        if self.current is None:
            return
        phase, number = details['phase'], details['attempt']
        attempts = self.current['attempts']
        if phase == 'start':
            attempts.append({'attempt':number, 'started_at':now(), 'status':'pending'})
        else:
            item = next((a for a in reversed(attempts) if a['attempt']==number), None)
            if item is None:
                item = {'attempt':number}
                attempts.append(item)
            item.update(public_copy({k:v for k,v in details.items() if k not in ('phase','attempt')}))
            item.update(status='completed' if phase=='complete' else 'error', ended_at=now())
        self.save()

    def response(self, reply):
        self.current.update(status='completed', ended_at=now(), response=public_copy({
            'message':reply.message, 'finish_reason':reply.finish_reason, 'usage':reply.usage,
            'usage_complete':getattr(reply, 'usage_complete', True),
            'transport_attempts':getattr(reply, 'transport_attempts', 1),
            'elapsed_seconds':reply.elapsed_seconds}),
            formatted_response=formatted_messages([reply.message])[0])
        self.current['response_discarded'] = reply.finish_reason not in ('stop','tool_calls')
        self.save()

    def tool_result(self, call, arguments, result, elapsed):
        if self.current is not None:
            self.current['tools'].append(public_copy({'call_id':call['id'],
                'name':call['function']['name'], 'arguments':arguments, 'result':result,
                'elapsed_seconds':elapsed, 'at':now()}))
            self.save()

    def stage(self, kind, value):
        self.data['stages'].append(public_copy({'at':now(), 'kind':kind, 'details':value}))
        self.save()

    def close_pending(self, status, error):
        for record in self.data['rounds']:
            if record['status']=='pending':
                record.update(status=status, ended_at=now(), error=public_copy(error))
            for attempt in record['attempts']:
                if attempt['status']=='pending':
                    attempt.update(status=status, ended_at=now(), error=public_copy(error))

    def finish(self, status, final_message, validation):
        self.close_pending(status, 'Request ended before a complete model response was received')
        self.data.update(status=status, ended_at=now(), final_message=public_copy(final_message),
                         validation=public_copy(validation))
        self.save()
