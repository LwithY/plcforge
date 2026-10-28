"""OpenAI Chat Completions wire protocol, with no runtime dependencies.

Corresponds to OpenAI(base_url=...).chat.completions.create(stream=True).
DeepSeek reasoning is replayed for tool trajectories but not printed as an answer.
"""
from __future__ import annotations

from dataclasses import dataclass
import errno
import http.client
import json
import socket
import ssl
import time
from urllib import error, request

from .config import Config
from .security import redact


class ModelError(RuntimeError):
    pass


class IncompleteStreamError(ModelError):
    """The model response ended before it could be committed to the agent."""


def retryable_transport(exc: BaseException) -> bool:
    if isinstance(exc,error.URLError):
        reason=exc.reason
        return retryable_transport(reason) if isinstance(reason,BaseException) else any(
            word in str(reason).lower() for word in ('unexpected_eof','eof occurred','timed out','connection reset'))
    if isinstance(exc,(ssl.SSLCertVerificationError,ssl.CertificateError,PermissionError)):
        return False
    if isinstance(exc,(IncompleteStreamError,ssl.SSLEOFError,ssl.SSLZeroReturnError,
                       socket.timeout,ConnectionError,http.client.IncompleteRead,http.client.RemoteDisconnected)):
        return True
    if isinstance(exc,ssl.SSLError):
        return any(word in str(exc).lower() for word in ('unexpected_eof','eof occurred'))
    if isinstance(exc,socket.gaierror):
        return exc.errno == socket.EAI_AGAIN
    if isinstance(exc,OSError):
        if exc.errno in {errno.EACCES,errno.EPERM,errno.EINVAL} or getattr(exc,'winerror',None)==10013:
            return False
        return exc.errno in {errno.ECONNRESET,errno.ECONNABORTED,errno.ECONNREFUSED,errno.ETIMEDOUT,
                             errno.EPIPE,errno.ENETRESET,errno.ENETUNREACH,errno.EHOSTUNREACH}
    return False


@dataclass
class Reply:
    message: dict
    usage: dict
    finish_reason: str
    elapsed_seconds: float
    transport_attempts: int = 1
    usage_complete: bool = True


def request_payload(config, messages, tools, max_output_tokens=None):
    payload = {'model':config.model, 'messages':messages, 'stream':True,
               'stream_options':{'include_usage':True}, 'max_tokens':max_output_tokens or config.max_output_tokens}
    if tools:
        payload.update(tools=tools, tool_choice='auto')
    return payload


def assemble_stream(events, on_text=None) -> tuple[dict, dict, str]:
    content, reasoning, calls, usage = [], [], {}, {}
    finish = ''
    for event in events:
        if event.get('error'):
            raise ModelError(redact(json.dumps(event['error'], ensure_ascii=False)))
        if event.get('usage'):
            usage = event['usage']
        for choice in event.get('choices') or []:
            if choice.get('index', 0) != 0:
                continue
            delta = choice.get('delta') or {}
            if delta.get('content'):
                content.append(delta['content'])
                if on_text:
                    on_text(delta['content'])
            if delta.get('reasoning_content'):
                reasoning.append(delta['reasoning_content'])
            for tool in delta.get('tool_calls') or []:
                index = tool.get('index', 0)
                dest = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                if tool.get('id'):
                    dest['id'] += tool['id']
                function = tool.get('function') or {}
                for key in ('name', 'arguments'):
                    dest['function'][key] += function.get(key) or ''
            finish = choice.get('finish_reason') or finish
    if not finish:
        raise IncompleteStreamError('Stream ended without a finish_reason; no tool side effects were executed')
    message = {'role': 'assistant', 'content': ''.join(content) or None}
    if reasoning:
        message['reasoning_content'] = ''.join(reasoning)
    if calls:
        message['tool_calls'] = [calls[k] for k in sorted(calls)]
        if any(not c['id'] or not c['function']['name'] for c in calls.values()):
            raise ModelError('Incomplete streamed tool call')
    return message, usage, finish


class OpenAIProvider:
    def __init__(self, config: Config, on_retry=None, on_attempt=None):
        self.config = config
        self.on_retry = on_retry
        self.on_attempt = on_attempt
        self.before_attempt = None

    def attempt_event(self, phase, attempt, **details):
        if self.on_attempt:
            self.on_attempt(dict(phase=phase, attempt=attempt+1, **details))

    def _retry(self, attempt, detail, deadline):
        if attempt >= self.config.max_retries:
            return False
        delay=min(8,self.config.retry_base_seconds*(2**attempt))
        if deadline and time.monotonic()+delay >= deadline:
            raise ModelError('Task wall time budget reached before retry')
        if self.on_retry:
            self.on_retry({'attempt':attempt+1,'next_attempt':attempt+2,
                           'max_attempts':self.config.max_retries+1,'delay_seconds':delay,
                           'reason':redact(detail)})
        time.sleep(delay)
        return True

    def complete(self, messages: list[dict], tools: list[dict], on_text=None,
                 deadline: float | None = None, max_output_tokens=None) -> Reply:
        config = self.config
        started = time.monotonic()
        for attempt in range(config.max_retries+1):
            cap = self.before_attempt(messages, tools) if self.before_attempt else max_output_tokens
            payload = request_payload(config,messages,tools,cap)
            data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            remaining = (deadline - time.monotonic()) if deadline else config.timeout_seconds
            if remaining <= 0:
                raise ModelError('Task wall time budget reached')
            headers = {'Content-Type':'application/json','Accept':'text/event-stream'}
            credential = config.key()
            if credential:
                headers['Authorization'] = 'Bearer '+credential
            req = request.Request(config.base_url.rstrip('/') + '/chat/completions', data=data,headers=headers)
            self.attempt_event('start',attempt)
            try:
                with request.urlopen(req, timeout=min(config.timeout_seconds, remaining)) as response:
                    def events():
                        pending = []
                        def decode_event(block):
                            try:
                                return json.loads(block)
                            except json.JSONDecodeError as exc:
                                raise IncompleteStreamError('Invalid or interrupted SSE JSON block') from exc
                        for raw in response:
                            if deadline and time.monotonic() >= deadline:
                                raise ModelError('Task wall time budget reached during stream')
                            try:
                                line = raw.decode('utf-8').rstrip('\r\n')
                            except UnicodeDecodeError as exc:
                                raise IncompleteStreamError('Invalid or interrupted UTF-8 stream record') from exc
                            if line.startswith('data:'):
                                pending.append(line[5:].lstrip())
                            elif not line and pending:
                                block = '\n'.join(pending)
                                pending.clear()
                                if block == '[DONE]':
                                    return
                                yield decode_event(block)
                        if pending and '\n'.join(pending) != '[DONE]':
                            yield decode_event('\n'.join(pending))
                    message, usage, finish = assemble_stream(events(), on_text)
                self.attempt_event('complete',attempt, finish_reason=finish)
                return Reply(message, usage, finish, time.monotonic()-started,
                             transport_attempts=attempt+1,usage_complete=(attempt==0))
            except error.HTTPError as exc:
                detail = redact(exc.read(4000).decode('utf-8', errors='replace'))
                self.attempt_event('error',attempt, error='HTTP '+str(exc.code)+': '+detail, http_status=exc.code)
                if exc.code in (408,429,500,502,503,504) and self._retry(attempt,'HTTP '+str(exc.code),deadline):
                    continue
                raise ModelError('HTTP %s after %d attempt(s): %s' % (exc.code,attempt+1,detail)) from exc
            except (error.URLError,OSError,http.client.HTTPException,IncompleteStreamError) as exc:
                # Retry the uncommitted MODEL request only. No tool executes
                # until a complete response is returned, so side effects from
                # previous rounds are never replayed by transport recovery.
                detail=redact(str(exc))
                self.attempt_event('error',attempt, error=detail)
                if retryable_transport(exc) and self._retry(attempt,detail,deadline):
                    continue
                raise ModelError('Model transport/protocol error after %d attempt(s): %s' % (attempt+1,detail)) from exc
            except (ValueError,UnicodeError) as exc:
                self.attempt_event('error',attempt, error=redact(str(exc)))
                raise ModelError(redact('Invalid model response: '+str(exc))) from exc
            except ModelError as exc:
                self.attempt_event('error',attempt, error=redact(str(exc)))
                raise
            except KeyboardInterrupt:
                self.attempt_event('error',attempt, error='Interrupted by user')
                raise
        raise ModelError('Model request retry limit reached')
