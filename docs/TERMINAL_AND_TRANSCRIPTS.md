# 终端进度与逐轮记录（0.1.2）

## 使用

旧进程中先 `/exit`，然后在任意工程目录重新输入 `plcforge`。当前版本 `0.1.6` 包含本页的进度与记录功能，并支持 npm 全局安装。

默认终端会显示：

```text
[模型] 第 1 轮 | deepseek-ai/DeepSeek-V4.1-Flash-1017
[当前] 等待模型响应
[模型说明] 先检查接口与优先级，再写程序并运行测试。
[模型] 本轮响应完成 (5.5s)
[当前] 写入文件 | src/PLC_PRG.st
[完成] 写入文件 | ok (0.0s)
  已保存 src/PLC_PRG.st
  [已完成] 实现启停逻辑
  [进行中] 验证急停优先级
[当前] 调用 CODESYS 编译
[完成] 调用 CODESYS 编译 | passed (18.8s)
```

示例中的说明和步骤仅用于展示格式。模型说明来自公开回答 `content`，系统要求它简要说明下一步和已核实的成果；如果该轮模型只返回工具调用，终端显示工具动作。原始隐藏推理 `reasoning_content` 不展示或导出，也不会被改名为“摘要”转发。模型等待期间显示带耗时的动态指示；重定向输出时使用普通文本，避免控制字符污染日志。

计划状态由模型维护；完成某个步骤不等于编译或测试通过。文件保存、测试数量、编译状态由真实工具结果展示。编程任务的完成检查保持不变。

模型公开回答按流接收并逐步显示。为防止密钥恰好跨越数据块边界，输出保留一小段末尾缓冲，短回答可能在本轮结束时一起显示。网络重试会明确提示该次未完成响应已丢弃。工具必须等模型响应完整接收后才执行。

## JSON 文件位置

每次自然语言请求生成独立文件：

```text
工程目录/.plcforge/sessions/会话ID/tasks/task-0001.json
工程目录/.plcforge/sessions/会话ID/tasks/task-0002.json
```

任务进行中原子更新文件，结束后写入最终状态；成功、模型错误、用户中断、等待补充要求以及达到预算都保留记录。`summary.json` 和 `.plcforge/last_run.json` 的 `transcript_path` 指向该文件；终端结束时也会打印绝对路径。交互命令 `/trace` 查看当前任务文件位置。

同一会话中再次输入新提示会创建新文件；`/retry` 或不带新提示的 `resume` 追加到原任务文件，轮次继续编号。恢复带有新提示时作为新请求记录。`/status`、`/files`、`/compile` 等直接本地命令不发起 LLM 请求，不创建自然语言任务文件。模型身份在本地回答时有 JSON，但 `rounds` 为空，并在 `local_responses` 说明来源。

`plcforge run ... --json` 仍只向标准输出写一个结果 JSON，不混入进度文字；逐轮文件照常生成。

## 字段

顶层包含 `session_id`、`task_number`、`prompt`、`initial_mode`、最终 `mode`、`status`、时间、`final_message`、`validation`、`stages` 和 `rounds`。

每一轮包含：

- `round`：该任务的第几轮模型请求，从 1 开始，恢复时不重置。
- `session_model_round`：会话内累计模型请求编号，失败的请求也计入；`loop_turn` 是本次执行循环编号。
- `request.method/url/body`：请求方法、地址及结构化请求体，包括模型、消息列表、工具定义和生成参数。不记录认证头。
- `formatted_messages`：请求消息的可读副本；工具结果 JSON 和工具参数字符串额外解析为 `formatted_content`、`formatted_arguments`，原协议字符串同时保留。
- `attempts`：网络实际尝试编号和状态。重试使用同一个 `request.body`；失败尝试记录错误和已收到的公开文本，并标记 `discarded`。尚未发出网络请求就失败时可为空。
- `response.message/finish_reason/usage`：完整接收后合并的模型回答、结束原因和用量；`formatted_response` 提供解析后的工具参数。该字段是组装后的 Chat 消息，并非原始 SSE 数据包。
- `response_discarded`：输出截断等情况下，虽然保留返回内容供审计，但不会执行其工具调用。
- `tools`：该轮实际处理的工具调用 ID、名称、参数、结果和耗时。工具结果也会出现在下一轮请求的 `role=tool` 消息中。

简化示例（省略工具定义和部分元数据）：

```json
{
  "task_number": 1,
  "status": "answered",
  "export_policy": {"credentials": "redacted", "hidden_reasoning": "omitted"},
  "rounds": [{
    "round": 1,
    "request": {
      "method": "POST",
      "url": "https://api-inference.modelscope.cn/v1/chat/completions",
      "body": {
        "model": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
        "messages": [{"role": "user", "content": "解释PLC扫描周期"}],
        "stream": true
      }
    },
    "attempts": [{"attempt": 1, "status": "completed"}],
    "response": {
      "message": {"role": "assistant", "content": "PLC循环采样输入、执行程序、更新输出。"},
      "finish_reason": "stop"
    }
  }]
}
```

## 导出边界与验证

文件保留公开请求/响应的结构，密钥脱敏，隐藏推理字段省略；`export_policy` 明示这些处理，所以它不是字节级原始网络抓包。运行时协议需要回传的推理字段继续留在原有私有会话状态中供恢复使用，不进入新导出文件。模型重试期间的未知 token 消耗仍标记 `usage_missing`。

实测使用真实模型完成了 S01 电机任务，记录 12 轮请求和工具调用，扫描测试 2/2 通过，真实 CODESYS 离线编译通过。验证索引在 `evidence/observability.json`。自动化测试另外覆盖任务文件隔离、跨进程恢复、失败/中断记录、工具调用顺序、流式输出和脱敏；不把模拟故障测试当作真实网络故障恢复证据。
