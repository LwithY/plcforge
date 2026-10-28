"""Public terminal progress and safe streaming of user-facing model content."""
from __future__ import annotations

import re
import shutil
import sys
import threading
import time
import unicodedata

from .security import redact, sensitive_values


class PublicTextBuffer:
    """Hold incomplete credential prefixes across SSE chunk boundaries."""
    def __init__(self):
        self.buffer = ''
        lengths = [len(v) for v in sensitive_values()]
        self.hold = max([128]+lengths)

    def feed(self, text='', final=False):
        self.buffer += text
        cut = len(self.buffer) if final else max(0,len(self.buffer)-self.hold)
        if not final:
            # Never split an otherwise unknown ModelScope token.
            for match in re.finditer(r'\bms-[a-zA-Z0-9-]*',self.buffer):
                if match.start() < cut < match.end():
                    cut = match.start()
            # Known secrets must be redacted BEFORE cutting a safe prefix.
            for value in sensitive_values():
                start = self.buffer.find(value)
                while start >= 0:
                    if start < cut < start+len(value):
                        cut = start
                    start = self.buffer.find(value,start+len(value))
        output, self.buffer = self.buffer[:cut], self.buffer[cut:]
        return re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', redact(output))


LABELS = {'list_files':'查看工程文件', 'read_file':'读取文件', 'write_file':'写入文件',
          'edit_file':'修改文件', 'search_code':'搜索代码', 'plc_knowledge':'查询 PLC 规则',
          'plan_tasks':'更新任务计划', 'lint_st':'检查 ST 语法', 'test_st':'运行扫描测试',
          'compile_st':'调用 CODESYS 编译', 'finish_task':'核验任务完成条件',
          'request_clarification':'确认缺失要求'}
LABELS['read_evidence'] = '读取开发测试证据'


class TerminalReporter:
    def __init__(self, stream=None):
        self.stream = stream or sys.stdout
        self.tty = bool(getattr(self.stream,'isatty',lambda:False)())
        self.worker = None
        self.stop = threading.Event()
        self.text_open = False
        self.label = ''
        self.width = max(20,shutil.get_terminal_size((80,24)).columns-1)

    def line(self, text):
        self.end_busy()
        if self.text_open:
            self.stream.write('\n')
            self.text_open = False
        self.stream.write(re.sub(r'[\x00-\x08\x0b-\x1f\x7f]','',redact(text))+'\n')
        self.stream.flush()

    def busy(self, label):
        self.end_busy()
        label = re.sub(r'[\x00-\x1f\x7f]','',redact(label))
        if self.text_open:
            self.stream.write('\n')
            self.text_open = False
        self.label = label
        if not self.tty:
            self.line('[当前] '+label)
            return
        self.stop.clear()
        started = time.monotonic()
        columns, compact = 0, ''
        for char in label:
            columns += 2 if unicodedata.east_asian_width(char) in 'WF' else 1
            if columns > self.width-18:
                compact += '...'
                break
            compact += char
        def tick():
            index = 0
            while not self.stop.is_set():
                self.stream.write('\r%s %s (%.0fs)' % ('|/-\\'[index%4],compact,time.monotonic()-started))
                self.stream.flush()
                index += 1
                self.stop.wait(.2)
        self.worker = threading.Thread(target=tick,daemon=True)
        self.worker.start()

    def end_busy(self):
        if self.worker is not None:
            self.stop.set()
            self.worker.join(timeout=1)
            self.worker = None
            self.stream.write('\r'+' '*self.width+'\r')
            self.stream.flush()

    def __call__(self, kind, value):
        if kind=='request_started' and 'budget_policy' in value:
            self.line('[预算] 会话累计上限 %s | 已计量/估算 %s token；恢复沿用已保存的预算。' % (
                value['budget_policy']['total_token_budget'],value['consumed_tokens']))
        elif kind=='model_start':
            self.line('[模型] 第 %d 轮 | %s' % (value['round'],value['model']))
            self.busy('等待模型响应')
        elif kind=='model_attempt_start' and value['attempt']>1:
            self.busy('等待模型重试响应')
        elif kind=='model_text':
            self.end_busy()
            if not self.text_open:
                self.stream.write('[模型说明] ')
                self.text_open = True
            self.stream.write(value['text'])
            self.stream.flush()
        elif kind=='model_end':
            self.line('[模型] 本轮响应完成 (%.1fs)' % value['elapsed_seconds'])
            usage = value.get('cumulative_usage', {})
            if usage:
                self.line('  累计输入 %s | 缓存命中 %s | 输出 %s | 估算剩余 %s token' % (
                    usage.get('prompt_tokens',0), value.get('cached_input_tokens',0), usage.get('completion_tokens',0),
                    value.get('remaining_estimated_tokens','unknown')))
                if not value.get('accounting', {}).get('usage_complete', True):
                    self.line('  用量不完整；预算含估算值，现金费用以服务商账单为准。')
        elif kind=='budget_preflight' and not value['allowed']:
            self.line('[停止] 预算预估不足：'+value['reason'])
        elif kind=='tool_start':
            name, args = value['name'],value.get('arguments',{})
            detail = args.get('path') or args.get('query') or args.get('pattern') or ''
            self.busy(LABELS.get(name,name)+(' | '+str(detail)[:120] if detail else ''))
        elif kind=='tool_result':
            result, name = value['result'],value['name']
            failed = bool(result.get('error')) or result.get('status') in ('failed','needs_repair')
            status = result.get('status') or ('error' if result.get('error') else 'ok')
            self.line('[%s] %s | %s (%.1fs)' % ('反馈' if failed else '完成',LABELS.get(name,name),status,value.get('elapsed_seconds',0)))
            if result.get('error'):
                self.line('  '+str(result['error'])[:300])
            if name in ('write_file','edit_file') and not result.get('error'):
                self.line('  已保存 '+result['path'])
                for field, label in (('local_syntax','语法检查'), ('interface','接口检查')):
                    check = result.get(field, {})
                    if check:
                        self.line('  文件已保存；'+label+'：'+check.get('status','unknown'))
                        for diagnostic in check.get('diagnostics', [])[:3]:
                            if isinstance(diagnostic, dict):
                                self.line('  %s:%s:%s [%s] %s' % (diagnostic.get('file'),diagnostic.get('line'),
                                    diagnostic.get('column'),diagnostic.get('code'),diagnostic.get('message')))
                if result.get('unresolved_diagnostics'):
                    self.line('  同一错误仍未解决；可按 diagnostic_history_id 查看上一版、差异和诊断。')
            if name=='plan_tasks' and not result.get('error'):
                for step in result.get('plan',[]):
                    mark = {'completed':'已完成','in_progress':'进行中','pending':'待执行'}.get(step['status'],step['status'])
                    self.line('  [%s] %s' % (mark,step['title']))
                self.line('  计划状态由模型更新；测试和编译结果以工具反馈为准。')
            if name=='test_st' and not result.get('error'):
                self.line('  扫描用例 %s/%s 通过' % (result.get('passed'),result.get('total')))
            if name=='compile_st' and not result.get('error'):
                self.line('  CODESYS: '+str(result.get('status')))
        elif kind=='compaction':
            self.line('[进展] 上下文压缩 %s → %s 字符' % (value.get('before_chars','?'),value.get('after_chars','?')))
        elif kind=='validation_start':
            self.busy('核验测试和 CODESYS 编译结果，必要时重新执行')
        elif kind=='validation_gate':
            self.line('[核验] '+value['status'])
        elif kind=='truncated_response':
            self.line('[反馈] 本轮输出不完整，未执行工具；正在重新生成')
        elif kind=='error':
            self.line('[错误] '+value['message'])
        elif kind=='model_retry':
            self.line('[重试] 本次未完成响应已丢弃，%.1f 秒后尝试 %d/%d' %
                      (value['delay_seconds'],value['next_attempt'],value['max_attempts']))
        elif kind=='completed':
            self.line('[任务] '+value['status'])

    def close(self):
        self.end_busy()
        if self.text_open:
            self.stream.write('\n')
            self.stream.flush()
            self.text_open = False
