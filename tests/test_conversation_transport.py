"""Regression checks for question routing and interrupted API responses."""
from __future__ import annotations
from contextlib import redirect_stdout
import http.client
import io
import json
import os
from pathlib import Path
import ssl
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib import error

from plcforge.agent import Agent
from plcforge.cli import main, print_run
from plcforge.config import Config
from plcforge.intent import asks_model_identity, request_mode
from plcforge.provider import ModelError, OpenAIProvider, Reply
from plcforge.tasks import load_task, task_prompt

ROOT=Path(__file__).resolve().parents[1]
MOTOR=(ROOT/'examples/reference_solutions/S01.st').read_text(encoding='utf-8')


def call(name,args,identifier='call_1'):
    return {'role':'assistant','content':None,'tool_calls':[{'id':identifier,'type':'function',
            'function':{'name':name,'arguments':json.dumps(args)}}]}


class RecordingProvider:
    def __init__(self,*responses):
        self.responses=iter(responses)
        self.requests=[]
    def complete(self,messages,tools,on_text=None,deadline=None):
        self.requests.append((list(messages),list(tools)))
        value=next(self.responses)
        if isinstance(value,BaseException):
            raise value
        return Reply(value,{'prompt_tokens':5,'completion_tokens':5,'total_tokens':10},
                     'tool_calls' if value.get('tool_calls') else 'stop',0.0)


class ConversationTests(unittest.TestCase):
    def setUp(self):
        parent=(ROOT/'.plcforge/test-temp').resolve()
        assert parent.is_relative_to(ROOT.resolve())
        parent.mkdir(parents=True,exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(dir=parent)
        self.workspace=Path(self.temporary.name)
    def tearDown(self):
        self.temporary.cleanup()
    def test_identity_is_answered_from_config_without_network_or_gate(self):
        provider=RecordingProvider()
        agent=Agent(Config(model='configured-provider/model'),self.workspace,provider=provider)
        with patch.object(agent.registry,'final_gate',side_effect=AssertionError('No PLC gate for identity')):
            result=agent.run('你是什么模型？')
        self.assertEqual(result['status'],'answered')
        self.assertEqual(result['request_model_calls'],0)
        self.assertIn('configured-provider/model',result['final_message'])
        self.assertEqual(result['reply_origin'],'runtime_config')
        self.assertFalse((self.workspace/'src').exists())
    def test_explanation_finishes_in_one_response_without_artifacts(self):
        provider=RecordingProvider({'role':'assistant','content':'PLC reads inputs, executes, then updates outputs.'})
        agent=Agent(Config(),self.workspace,provider=provider)
        with patch.object(agent.registry,'final_gate',side_effect=AssertionError('No PLC gate for explanation')):
            result=agent.run('请解释 PLC 的扫描周期。')
        self.assertEqual(result['status'],'answered')
        self.assertEqual(result['request_model_calls'],1)
        self.assertFalse((self.workspace/'src').exists())
    def test_answer_does_not_validate_or_rewrite_existing_broken_source(self):
        (self.workspace/'src').mkdir()
        source=self.workspace/'src/PLC_PRG.st'
        source.write_text('deliberately invalid existing ST',encoding='utf-8')
        agent=Agent(Config(),self.workspace,provider=RecordingProvider({'role':'assistant','content':'TON is an on-delay timer.'}))
        agent.state['pending_implementation']=True
        with patch.object(agent.registry,'final_gate',side_effect=AssertionError('Not requested')):
            result=agent.run('什么是 TON 定时器？')
        self.assertEqual(result['status'],'answered')
        self.assertEqual(result['validation'],{})
        self.assertTrue(agent.state['pending_implementation'])
        self.assertEqual(source.read_text(encoding='utf-8'),'deliberately invalid existing ST')
    def test_model_cannot_write_in_a_read_only_answer_turn(self):
        provider=RecordingProvider(call('write_file',{'path':'src/UNWANTED.st','content':MOTOR}),
                                   {'role':'assistant','content':'Here is the explanation.'})
        agent=Agent(Config(),self.workspace,provider=provider)
        result=agent.run('解释一下扫描周期')
        self.assertEqual(result['status'],'answered')
        self.assertFalse((self.workspace/'src/UNWANTED.st').exists())
        self.assertNotIn('write_file',[t['function']['name'] for t in provider.requests[0][1]])
    def test_code_request_still_requires_generation_and_checks(self):
        task=load_task(ROOT/'data/tasks/S01.json')
        provider=RecordingProvider({'role':'assistant','content':'Done.'},
            call('write_file',{'path':'src/PLC_PRG.st','content':MOTOR}),
            call('finish_task',{'summary':'Locally verified.'}))
        result=Agent(Config(),self.workspace,task,provider).run(task_prompt(task))
        self.assertEqual(result['status'],'awaiting_codesys')
        self.assertEqual(result['validation']['tests']['status'],'passed')
        self.assertEqual(result['request_model_calls'],2)
    def test_ambiguous_request_with_real_writes_cannot_skip_gate(self):
        task=load_task(ROOT/'data/tasks/S01.json')
        message=call('write_file',{'path':'src/PLC_PRG.st','content':MOTOR},'source')
        message['tool_calls']+=call('write_file',{'path':'tests/test.json','content':json.dumps(task)},'tests')['tool_calls']
        provider=RecordingProvider(message,call('finish_task',{'summary':'Verified.'}))
        result=Agent(Config(),self.workspace,provider=provider).run('电机启停')
        self.assertEqual(result['status'],'awaiting_codesys')
        self.assertEqual(result['request_mode'],'code')
    def test_explicit_answer_mode_never_generates_code(self):
        agent=Agent(Config(),self.workspace,provider=RecordingProvider({'role':'assistant','content':'Design explanation.'}))
        result=agent.run('编写 PLC 电机程序',mode='answer')
        self.assertEqual(result['status'],'answered')
        self.assertFalse((self.workspace/'src').exists())
    def test_retry_preserves_original_question_and_does_not_append_it_twice(self):
        provider=RecordingProvider(ModelError('temporary transport failure'),
                                   {'role':'assistant','content':'TON delays the rising output.'})
        agent=Agent(Config(),self.workspace,provider=provider)
        self.assertEqual(agent.run('什么是 TON？')['status'],'model_error')
        result=agent.run('')
        self.assertEqual(result['status'],'answered')
        self.assertEqual(sum(m['role']=='user' for m in agent.state['messages']),1)
        self.assertFalse((self.workspace/'src').exists())
    def test_clarification_pauses_a_code_request_without_fake_completion(self):
        provider=RecordingProvider(call('request_clarification',{'question':'What are the input names?'}))
        agent=Agent(Config(),self.workspace,provider=provider)
        with patch.object(agent.registry,'final_gate',side_effect=AssertionError('Awaiting input')):
            result=agent.run('编写 PLC 电机控制程序')
        self.assertEqual(result['status'],'needs_input')
        self.assertTrue(agent.state['pending_implementation'])
    def test_answer_is_visible_in_default_cli_output(self):
        with redirect_stdout(io.StringIO()) as stream:
            print_run({'status':'answered','final_message':'configured-model-name'})
        self.assertIn('configured-model-name',stream.getvalue())
        self.assertNotIn('CODESYS:',stream.getvalue())
    def test_cli_retry_recovers_the_same_question(self):
        provider=RecordingProvider(ModelError('temporary connection failure'),
                                   {'role':'assistant','content':'Recovered explanation.'})
        agent=Agent(Config(),self.workspace,provider=provider)
        with patch('plcforge.cli.Agent',return_value=agent),patch('plcforge.cli.load_env'), \
                patch('builtins.input',side_effect=['什么是扫描周期？','/retry','/exit']), \
                redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['chat','--workspace',str(self.workspace)]),0)
        self.assertIn('Recovered explanation.',output.getvalue())
        self.assertEqual(len(provider.requests),2)
    def test_intent_examples(self):
        for text in ('你是什么模型？','你当前用的是什么模型？','What model are you using?'):
            with self.subTest(text=text):
                self.assertTrue(asks_model_identity(text))
        for text in ('请编写 PLC 电机程序','帮我写一个启停程序好吗？','Can you implement a PLC latch?',
                     '不要修改测试，修复PLC程序','修复PLC代码并解释原理',
                     'Explain this PLC code and then fix the program'):
            with self.subTest(text=text):
                self.assertEqual(request_mode(text),'code')
        for text in ('请解释 PLC 的扫描周期','你好','你能用哪些工具？','Explain how PLC code generation works',
                     '你写的代码是什么意思？','请用两句话解释PLC扫描周期，不要创建或修改文件。'):
            with self.subTest(text=text):
                self.assertEqual(request_mode(text),'answer')


class FakeResponse:
    def __init__(self,events,fail=None,done=True):
        self.events=events
        self.fail=fail
        self.done=done
    def __enter__(self): return self
    def __exit__(self,*args): return False
    def __iter__(self):
        for event in self.events:
            yield ('data: '+json.dumps(event)+'\n').encode('utf-8')
            yield b'\n'
        if self.fail:
            raise self.fail
        if self.done:
            yield b'data: [DONE]\n\n'


def response(message=None):
    message=message or {'role':'assistant','content':'ok'}
    delta={k:v for k,v in message.items() if k not in ('role','tool_calls')}
    if message.get('tool_calls'):
        delta['tool_calls']=[dict(tool,index=i) for i,tool in enumerate(message['tool_calls'])]
    return FakeResponse([{'choices':[{'delta':delta,'finish_reason':'tool_calls' if message.get('tool_calls') else 'stop'}]},
                         {'choices':[],'usage':{'total_tokens':10}}])


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.environment=patch.dict(os.environ,{'MODELSCOPE_API_KEY':'unit-fixture-not-a-real-token'})
        self.environment.start()
        self.sleeps=patch('plcforge.provider.time.sleep')
        self.sleep=self.sleeps.start()
    def tearDown(self):
        self.sleeps.stop()
        self.environment.stop()
    def test_ssl_eof_before_response_retries_with_identical_payload(self):
        retries=[]
        provider=OpenAIProvider(Config(max_retries=2),on_retry=retries.append)
        failure=error.URLError(ssl.SSLEOFError(8,'UNEXPECTED_EOF_WHILE_READING'))
        with patch('plcforge.provider.request.urlopen',side_effect=[failure,response()]) as send:
            reply=provider.complete([{'role':'user','content':'Explain PLC'}],[])
        self.assertEqual(reply.message['content'],'ok')
        self.assertEqual(send.call_count,2)
        self.assertEqual(send.call_args_list[0].args[0].data,send.call_args_list[1].args[0].data)
        self.assertEqual(reply.transport_attempts,2)
        self.assertFalse(reply.usage_complete)
        self.assertEqual(len(retries),1)
    def test_missing_finish_reason_retries(self):
        incomplete=FakeResponse([{'choices':[{'delta':{'content':'partial'}}]}],done=False)
        with patch('plcforge.provider.request.urlopen',side_effect=[incomplete,response()]) as send:
            reply=OpenAIProvider(Config(max_retries=1)).complete([{'role':'user','content':'Q'}],[])
        self.assertEqual(reply.message['content'],'ok')
        self.assertEqual(send.call_count,2)
    def test_a_truncated_json_record_retries_without_committing_it(self):
        class CutJSON(FakeResponse):
            def __iter__(self):
                yield b'data: {"choices": [{"delta":'
        with patch('plcforge.provider.request.urlopen',side_effect=[CutJSON([]),response()]) as send:
            reply=OpenAIProvider(Config(max_retries=1)).complete([{'role':'user','content':'Q'}],[])
        self.assertEqual(reply.message['content'],'ok')
        self.assertEqual(send.call_count,2)
    def test_ssl_eof_exhaustion_is_bounded(self):
        failure=error.URLError(ssl.SSLEOFError(8,'UNEXPECTED_EOF_WHILE_READING'))
        with patch('plcforge.provider.request.urlopen',side_effect=failure) as send:
            with self.assertRaisesRegex(ModelError,'after 3 attempt'):
                OpenAIProvider(Config(max_retries=2)).complete([{'role':'user','content':'Q'}],[])
        self.assertEqual(send.call_count,3)
        self.assertEqual([c.args[0] for c in self.sleep.call_args_list],[1.0,2.0])
    def test_certificate_verification_failure_is_not_retried(self):
        failure=error.URLError(ssl.SSLCertVerificationError(1,'CERTIFICATE_VERIFY_FAILED'))
        with patch('plcforge.provider.request.urlopen',side_effect=failure) as send:
            with self.assertRaises(ModelError):
                OpenAIProvider(Config()).complete([{'role':'user','content':'Q'}],[])
        self.assertEqual(send.call_count,1)
        self.sleep.assert_not_called()
    def test_http_auth_failure_is_not_retried(self):
        failure=error.HTTPError('https://example.invalid',401,'Unauthorized',{},io.BytesIO(b'invalid key'))
        with patch('plcforge.provider.request.urlopen',side_effect=failure) as send:
            with self.assertRaisesRegex(ModelError,'HTTP 401'):
                OpenAIProvider(Config()).complete([{'role':'user','content':'Q'}],[])
        self.assertEqual(send.call_count,1)
    def test_server_temporary_failure_retries(self):
        failure=error.HTTPError('https://example.invalid',503,'busy',{},io.BytesIO(b'busy'))
        with patch('plcforge.provider.request.urlopen',side_effect=[failure,response()]) as send:
            self.assertEqual(OpenAIProvider(Config()).complete([{'role':'user','content':'Q'}],[]).message['content'],'ok')
        self.assertEqual(send.call_count,2)
    def test_incomplete_http_read_retries(self):
        failed=FakeResponse([],fail=http.client.IncompleteRead(b'data'))
        with patch('plcforge.provider.request.urlopen',side_effect=[failed,response()]) as send:
            self.assertEqual(OpenAIProvider(Config()).complete([{'role':'user','content':'Q'}],[]).message['content'],'ok')
        self.assertEqual(send.call_count,2)
    def test_retry_respects_wall_deadline(self):
        failure=error.URLError(ssl.SSLEOFError(8,'UNEXPECTED_EOF_WHILE_READING'))
        with patch('plcforge.provider.time.monotonic',return_value=100.0), \
                patch('plcforge.provider.request.urlopen',side_effect=failure) as send:
            with self.assertRaisesRegex(ModelError,'budget'):
                OpenAIProvider(Config()).complete([{'role':'user','content':'Q'}],[],deadline=100.5)
        self.assertEqual(send.call_count,1)
        self.sleep.assert_not_called()
    def test_interrupted_tool_response_is_not_executed_or_replayed(self):
        parent=(ROOT/'.plcforge/test-temp').resolve()
        parent.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as tmp:
            task=load_task(ROOT/'data/tasks/S01.json')
            unwanted=call('write_file',{'path':'src/UNWANTED.st','content':MOTOR})
            delta={'tool_calls':[dict(unwanted['tool_calls'][0],index=0)]}
            first=FakeResponse([{'choices':[{'delta':delta}]}],fail=ssl.SSLEOFError(8,'UNEXPECTED_EOF_WHILE_READING'))
            valid=call('write_file',{'path':'src/PLC_PRG.st','content':MOTOR})
            finish=call('finish_task',{'summary':'Local tests passed.'},'finish')
            with patch('plcforge.provider.request.urlopen',side_effect=[first,response(valid),response(finish)]) as send:
                result=Agent(Config(max_retries=1),Path(tmp),task).run(task_prompt(task))
            self.assertEqual(result['status'],'awaiting_codesys')
            self.assertEqual(result['tool_calls'],1)
            self.assertEqual(result['transport_retries'],1)
            self.assertFalse((Path(tmp)/'src/UNWANTED.st').exists())
            self.assertEqual(send.call_count,2)


if __name__=='__main__':
    unittest.main()
