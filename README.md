# PLCForge

面向 PLC Structured Text（ST）的命令行编程 agent，支持自定义 LLM、工程文件读写、扫描测试和真实 CODESYS 离线编译反馈。

## 通过 npm 安装

Windows 10/11，已安装 Node.js 20+：

```powershell
npm install -g plcforge@latest
```

然后在任意工程文件夹打开终端：

```powershell
plcforge
```

当前文件夹就是工程工作区。安装器优先复用 Python 3.11+；没有合适版本时自动下载官方 Windows 便携运行时并校验 SHA-256。无需 pip 安装依赖。首次安装需要联网，后续使用保留本地运行时和配置。

升级也使用 `npm install -g plcforge@latest`，无需先卸载。同一个 npm 全局安装目录中的旧版会被替换，保留模型设置、密钥、CODESYS 配置及工程文件。当前发布版为 `0.1.6`；如需固定该版本，可运行 `npm install -g plcforge@0.1.6`。升级后退出已有聊天并重新启动，用 `plcforge --version` 确认版本。`latest` 在执行安装命令时解析；后续更新需再次执行该命令。

Windows x64 已实测；自动运行时清单同时提供 Windows ARM64。CODESYS 本身需要用户另行安装。

## 选择自己的模型

编辑用户目录下的 `.plcforge/settings.json`：

```json
{
  "base_url": "https://你的服务地址/v1",
  "model": "供应商提供的模型ID",
  "api_key": "填写你自己的API密钥"
}
```

支持 OpenAI 兼容 Chat Completions/SSE；PLC 编程任务需要模型支持工具调用。也可以通过 `api_key_env` 指定密钥环境变量。包内不提供 API 密钥。`/settings` 显示实际选中的文件，`/settings reload` 重新加载并建立新会话。

```powershell
plcforge --settings 'D:\configs\my-model.json'
plcforge settings init
```

工程 `.plcforge/settings.json` 优先于用户默认配置。用户配置、运行时和模板保存在 npm 安装目录之外，升级时保留。详见 [模型设置](docs/LLM_SETTINGS.md)。

## 使用与进度

```text
plc> 编写电机启停自锁程序，Stop和EStop优先于Start，添加扫描测试
[模型] 第 1 轮 ...
[当前] 等待模型响应
[模型说明] ...
[当前] 写入文件 | src/PLC_PRG.st
[完成] 写入文件 | ok
[当前] 运行扫描测试
[当前] 调用 CODESYS 编译
```

终端展示面向用户的简要说明、工具动作、计划阶段和工具核验结果。隐藏思维链不展示或导出。

每次自然语言请求生成一个独立 JSON，按轮次记录发送的消息、工具定义、模型公开回答、工具结果及重试。终端会打印路径，也可用 `/trace` 查看：

```text
工程目录/.plcforge/sessions/会话ID/tasks/task-0001.json
```

密钥脱敏，隐藏推理字段省略。失败和中断也保留记录。`/retry` 继续原任务；新提示创建新文件。详见 [记录格式](docs/TERMINAL_AND_TRANSCRIPTS.md)。

常用命令：`/help`、`/model`、`/settings`、`/settings reload`、`/files`、`/plan`、`/compile`、`/test`、`/retry`、`/trace`、`/exit`。

## CODESYS 接入

安装器会检测已安装且带 Scripting 的 CODESYS，准备厂商标准模板的副本，并通过真实离线编译探针核验接入。安装器不会登录 PLC、下载程序或启动设备。

若稍后安装了 CODESYS，或使用自定义安装位置：

```powershell
plcforge setup --codesys 'E:\CODESYS\YourInstalledVersion'
plcforge doctor
```

`ready_verified` 表示本机工具链已实际验证。当前适配器在 CODESYS 3.5 SP22 Patch 4 上实测，其他版本由本机探针检查；未安装编译器时会明确标为待编译。

## 单次任务

```powershell
plcforge run -p 'Implement a PLC motor latch with stop priority'
plcforge run --task data/tasks/S01.json --workspace ./motor
plcforge run -p '解释PLC扫描周期' --json
plcforge resume 会话ID --workspace ./motor
```

通过 npm 安装后，示例题目在包内；可使用自己的题目文件，或在源码仓库运行上述 `data/tasks` 示例。

本地测试器支持 ST 的一个子集，扫描测试和有限轨迹 LTL 监测不等同于真实 PLC 运行测试或完整形式验证。`completed` 必须有匹配当前源代码的真实 CODESYS 通过结果；普通问答使用 `answered` 状态。当前没有“性能超过 Claude Code”的四组对照实验结论。

0.1.5 起，宿主在文件修改后自动核验并保存产物，测试轨迹单独落盘，模型默认只收到摘要。结果分别记录产物验证、Agent 停止原因、需求审核与报告状态。会话预算跨请求和恢复累计；独立评测需放在模型工作区之外。使用方法、预算恢复、P01 对照实测与验证范围见 [Agent 改进与验收](docs/AGENT_IMPROVEMENTS_0.1.5.md)。

## 开发

```powershell
npm test
python -m unittest discover -s tests -v
npm pack
```

源码无需第三方 Python 依赖。源代码运行可用 `python -m plcforge`；Windows 也可使用 `plcforge.cmd`。npm 包仅包含运行代码、规则、题目和用户说明，不包含开发者凭据、本机编译器、原始会话或上游复原版源码。

安装细节、环境变量、升级及故障处理见 [npm 安装说明](docs/NPM_INSTALL.md)。本项目由 Claude Code 复原版的控制流概念启发，PLCForge 运行时代码为独立实现；它不是 Anthropic 官方产品。来源边界见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

MIT License.
