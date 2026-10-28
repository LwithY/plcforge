"""Round-trip audit, task isolation, retry recovery and public terminal progress."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import ssl
import tempfile
import unittest
from unittest.mock import patch
from urllib import error

from plcforge.agent import Agent
from plcforge.cli import main, print_run
from plcforge.config import Config
from plcforge.progress import PublicTextBuffer, TerminalReporter
from plcforge.provider import ModelError, Reply
from test_conversation_transport import FakeResponse, RecordingProvider, call, response

ROOT = Path(__file__).resolve().parents[1]


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        directory = ROOT/'.plcforge/test-temp'
        directory.mkdir(parents=True,exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=directory)
        self.workspace = Path(self.tmp.name)
        self.env = patch.dict(os.environ,{'MODELSCOPE_API_KEY':'test-observability-credential'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def read(self, result):
        return json.loads(Path(result['transcript_path']).read_text(encoding='utf-8'))

    def test_numbered_rounds_capture_real_wire_request_and_public_reply(self):
        first = call('list_files',{},'files')
        first.update(content='先检查工程文件。',reasoning_content='PRIVATE_COT_FIXTURE')
        second = {'role':'assistant','content':'当前工程为空。','reasoning_content':'SECOND_PRIVATE_FIXTURE'}
        with patch('plcforge.provider.request.urlopen',side_effect=[response(first),response(second)]) as send:
            result = Agent(Config(),self.workspace).run('查看当前工程并解释文件用途')
        trace = self.read(result)
        self.assertEqual([r['round'] for r in trace['rounds']],[1,2])
        for index,record in enumerate(trace['rounds']):
            actual = json.loads(send.call_args_list[index].args[0].data)
            if index==0:
                self.assertEqual(record['request']['body'],actual)
            self.assertEqual(record['request']['body']['model'],actual['model'])
            self.assertEqual(record['attempts'][0]['status'],'completed')
        wire_second = json.loads(send.call_args_list[1].args[0].data)
        self.assertIn('PRIVATE_COT_FIXTURE',json.dumps(wire_second))  # Required private protocol replay remains intact.
        exported = Path(result['transcript_path']).read_text(encoding='utf-8')
        self.assertNotIn('PRIVATE_COT_FIXTURE',exported)
        self.assertNotIn('SECOND_PRIVATE_FIXTURE',exported)
        self.assertNotIn('reasoning_content',exported)
        self.assertNotIn('test-observability-credential',exported)
        self.assertEqual(trace['rounds'][0]['formatted_response']['tool_calls'][0]['function']['formatted_arguments'],{})
        self.assertEqual(trace['rounds'][0]['tools'][0]['result'],{'files':[]})
        self.assertEqual(trace['rounds'][1]['formatted_messages'][-1]['formatted_content'],{'files':[]})
        # Stored round 1 is a snapshot, not a reference to the mutated conversation.
        self.assertEqual(len(trace['rounds'][0]['request']['body']['messages']),2)

    def test_each_user_request_has_its_own_json_in_one_session(self):
        provider = RecordingProvider({'role':'assistant','content':'First answer.'},
                                     {'role':'assistant','content':'Second answer.'})
        agent = Agent(Config(),self.workspace,provider=provider)
        first = agent.run('解释扫描周期')
        snapshot = Path(first['transcript_path']).read_bytes()
        second = agent.run('解释 TON')
        self.assertNotEqual(first['transcript_path'],second['transcript_path'])
        self.assertEqual(Path(first['transcript_path']).read_bytes(),snapshot)
        self.assertEqual(self.read(first)['task_number'],1)
        self.assertEqual(self.read(second)['task_number'],2)
        self.assertEqual(self.read(second)['rounds'][0]['round'],1)

    def test_retry_attempts_record_discarded_public_text_without_executing_tools(self):
        failed = FakeResponse([{'choices':[{'delta':{'content':'This response was interrupted.',
                          'reasoning_content':'PRIVATE_RETRY_FIXTURE'}}]}],fail=ssl.SSLEOFError(8,'EOF occurred'))
        output = io.StringIO()
        with patch('plcforge.provider.request.urlopen',side_effect=[failed,response()]), \
                patch('plcforge.provider.time.sleep'):
            result = Agent(Config(max_retries=1),self.workspace,emit=TerminalReporter(output)).run('解释扫描周期')
        record = self.read(result)['rounds'][0]
        self.assertEqual([a['status'] for a in record['attempts']],['error','completed'])
        self.assertEqual(record['attempts'][0]['partial_public_content'],'This response was interrupted.')
        self.assertTrue(record['attempts'][0]['discarded'])
        self.assertEqual(record['response']['message']['content'],'ok')
        self.assertNotIn('PRIVATE_RETRY_FIXTURE',output.getvalue())
        self.assertNotIn('PRIVATE_RETRY_FIXTURE',Path(result['transcript_path']).read_text(encoding='utf-8'))

    def test_failed_request_is_saved_and_resume_appends_rounds_to_same_file(self):
        provider = RecordingProvider(ModelError('temporary failure'),{'role':'assistant','content':'Recovered.'})
        agent = Agent(Config(),self.workspace,provider=provider)
        first = agent.run('解释扫描周期')
        failed = self.read(first)
        self.assertEqual(failed['status'],'model_error')
        self.assertEqual(failed['rounds'][0]['error'],'temporary failure')
        recovered = agent.run('')
        self.assertEqual(first['transcript_path'],recovered['transcript_path'])
        trace = self.read(recovered)
        self.assertEqual([r['round'] for r in trace['rounds']],[1,2])
        self.assertEqual([r['session_model_round'] for r in trace['rounds']],[1,2])
        self.assertEqual(trace['status'],'answered')

    def test_keyboard_interruption_still_finalizes_task_json(self):
        agent = Agent(Config(),self.workspace,provider=RecordingProvider(KeyboardInterrupt()))
        result = agent.run('解释扫描周期')
        self.assertEqual(result['status'],'interrupted')
        self.assertEqual(self.read(result)['rounds'][0]['status'],'interrupted')

    def test_resume_in_a_new_process_keeps_the_original_trace_and_round_numbers(self):
        first_agent = Agent(Config(),self.workspace,provider=RecordingProvider(ModelError('connection reset')))
        first = first_agent.run('解释扫描周期')
        resumed = Agent(Config(),self.workspace,session_id=first_agent.session_id,
                        provider=RecordingProvider({'role':'assistant','content':'Recovered in a new process.'}))
        result = resumed.run('')
        self.assertEqual(first['transcript_path'],result['transcript_path'])
        self.assertEqual([r['round'] for r in self.read(result)['rounds']],[1,2])

    def test_credentials_are_redacted_in_nested_request_and_response_fields(self):
        secret = os.environ['MODELSCOPE_API_KEY']
        provider = RecordingProvider({'role':'assistant','content':'Echo: '+secret})
        result = Agent(Config(),self.workspace,provider=provider).run('解释扫描周期，测试占位值 '+secret)
        content = Path(result['transcript_path']).read_text(encoding='utf-8')
        self.assertNotIn(secret,content)
        self.assertIn('[REDACTED]',content)

    def test_identity_records_local_response_and_zero_network_rounds(self):
        result = Agent(Config(),self.workspace,provider=RecordingProvider()).run('你是什么模型？')
        trace = self.read(result)
        self.assertEqual(trace['rounds'],[])
        self.assertEqual(trace['local_responses'][0]['source'],'runtime_config')
        self.assertEqual(trace['status'],'answered')

    def test_progress_streams_public_content_before_transport_completes(self):
        content = '这是面向用户的简要说明。'*30
        observed = []
        class InspectResponse(FakeResponse):
            def __iter__(self):
                yield ('data: '+json.dumps({'choices':[{'delta':{'reasoning_content':'PRIVATE_STREAM_FIXTURE',
                                                'content':content}}]})+'\n').encode()
                yield b'\n'
                self_test.assertTrue(any(kind=='model_text' for kind,_ in observed))
                yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n'
                yield b'\n'
                yield b'data: [DONE]\n\n'
        self_test = self
        with patch('plcforge.provider.request.urlopen',return_value=InspectResponse([])):
            result = Agent(Config(),self.workspace,emit=lambda k,v:observed.append((k,v))).run('解释扫描周期')
        displayed = ''.join(v['text'] for k,v in observed if k=='model_text')
        self.assertEqual(displayed,content)
        self.assertNotIn('PRIVATE_STREAM_FIXTURE',json.dumps(observed))
        self.assertTrue(result['final_message_displayed'])

    def test_tool_start_is_emitted_before_execution_and_plan_stages_are_visible(self):
        plan = [{'title':'检查工程文件','status':'in_progress'}, {'title':'解释结构','status':'pending'}]
        provider = RecordingProvider(call('plan_tasks',{'steps':plan}),{'role':'assistant','content':'Done.'})
        events, stream = [],io.StringIO()
        reporter = TerminalReporter(stream)
        def emit(kind,value):
            events.append(kind)
            reporter(kind,value)
        agent = Agent(Config(),self.workspace,provider=provider,emit=emit)
        original = agent.registry.execute
        def execute(name,args):
            self.assertEqual(events[-1],'tool_start')
            return original(name,args)
        with patch.object(agent.registry,'execute',side_effect=execute):
            result = agent.run('电机启停方案')
        output = stream.getvalue()
        self.assertIn('[进行中] 检查工程文件',output)
        self.assertIn('[待执行] 解释结构',output)
        self.assertIn('以工具反馈为准',output)
        self.assertTrue(any(s['kind']=='tool_result' for s in self.read(result)['stages']))

    def test_json_cli_stdout_remains_one_json_object(self):
        agent = Agent(Config(),self.workspace,provider=RecordingProvider({'role':'assistant','content':'Answer.'}))
        with patch('plcforge.cli.Agent',return_value=agent),patch('plcforge.cli.load_env'), \
                redirect_stdout(io.StringIO()) as output:
            code = main(['run','--workspace',str(self.workspace),'-p','解释扫描周期','--json'])
        value = json.loads(output.getvalue())
        self.assertEqual(code,0)
        self.assertTrue(Path(value['transcript_path']).is_file())
        self.assertEqual(value['final_message'],'Answer.')

    def test_streamed_answer_is_not_printed_twice_but_transcript_path_is_shown(self):
        with redirect_stdout(io.StringIO()) as output:
            print_run({'status':'answered','final_message':'Already visible.',
                       'final_message_displayed':True,'transcript_path':'task-0001.json'})
        self.assertNotIn('Already visible.',output.getvalue())
        self.assertIn('task-0001.json',output.getvalue())

    def test_redaction_survives_secret_split_across_stream_chunks(self):
        buffer = PublicTextBuffer()
        token = os.environ['MODELSCOPE_API_KEY']
        parts = ['prefix '+token[:9],token[9:]+' suffix '+'x'*400]
        printed = ''.join(buffer.feed(part) for part in parts)+buffer.feed(final=True)
        self.assertNotIn(token,printed)
        self.assertIn('[REDACTED]',printed)
        unknown = 'ms-'+'a'*300
        buffer = PublicTextBuffer()
        printed = buffer.feed('prefix '+unknown+' '+'suffix '*25)+buffer.feed(final=True)
        self.assertNotIn(unknown,printed)
        self.assertNotIn('a'*20,printed)

    def test_tty_spinner_stops_cleanly(self):
        class FakeTTY(io.StringIO):
            def isatty(self): return True
        tty = FakeTTY()
        reporter = TerminalReporter(tty)
        reporter.busy('调用 CODESYS 编译')
        reporter.close()
        self.assertIsNone(reporter.worker)
        self.assertIn('\r',tty.getvalue())


if __name__=='__main__':
    unittest.main()
