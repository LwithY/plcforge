# PLCForge 0.1.5 Agent 改进与验收

本版按 2026-10-28 交接方案实现上下文减重、宿主自动验收、预算控制、可定位诊断、需求合同和独立评测。实现与验证结果保存在 [验收记录](validation/agent-v0.1.5.json)。

## 工具结果与上下文

`test_st` 默认返回不超过 2,000 字节的摘要：通过数、总数、源码哈希、测试集哈希、扫描配置、引擎版本、报告 ID 和位置。失败摘要包含首个分歧扫描号（`first_divergence_scan` 从 0 开始，`scan_number` 从 1 开始）、输入、期望、实际及必要的前一状态。特别大的接口通过报告 ID 读取详情。

完整开发测试轨迹保存在工作区 `.plcforge/reports/<report_id>.json`，不进入会话检查点。模型可调用 `read_evidence` 按页读取开发证据；独立评测没有该工具入口。测试复用同时绑定全部源文件、入口文件、测试/规格、扫描周期和引擎版本，并检查证据文件存在且哈希匹配。

上下文检查点保留目标、文件哈希、未解决问题和精简验证状态。原始任务和全部用户补充保留；工具调用与结果保持配对。压缩测量 `before_chars`、`after_chars`、策略和存档位置，只有长度严格减小时才替换。较大的工具结果会裁剪或改为文件引用；不能缩短的需求会明确触发上下文限制。旧上下文另存于会话 `context/`。

## 自动验收与结果分层

整批模型工具执行完毕后，宿主核对最终源码，执行接口、语法、开发扫描测试和真实编译。一次响应中先 `finish_task` 后修改文件时，仍以最后源码重新验收。通过后宿主保存准确的源码快照和验证清单，不再请求模型更新计划、归档或调用完成工具。

结果同时包含：

- `status` / `stop_reason`：真实的 Agent 停止原因。预算停止继续记为 `token_budget`。
- `artifact_status`：产物的 `completed`、`needs_repair`、`awaiting_codesys` 或 `generated_validation_incomplete`。
- `requirement_review`：需求审核状态，P01 暂定合同为 `pending_confirmation`。
- `report_status` 与 `artifact.directory`：结果报告和源码快照的位置。
- `validation.layers`：语法、ST 子集扫描、有限轨迹 LTL、真实编译、CODESYS 运行时、实体 PLC 的分别状态。

编译器不可用、子集不支持、必要测试缺失或测试失败都不会报告全部验证通过。真实编译的复用继续要求源码哈希与编译器指纹一致；指纹覆盖模板、编译器、桥接脚本和编译配置。

## 预算与恢复

`total_token_budget` 的作用范围是整个会话，包括新请求、重试和恢复。`request_token_budget` 是每次 API 尝试的额外输入加输出估算上限，0 表示不另设限制。`max_output_tokens` 是单次输出上限；`max_turns` 和 `wall_seconds` 每次 `run` / `resume` 重新计时。

每次发送和重试前，按 UTF-8 字节数和安全系数估算输入，预留输出，并按余量降低请求的 `max_tokens`。不足最小输出预留时不发请求，仍保存会话并执行本地收尾。字符估算不能提供严格账单上限，严格费用限制需同时使用服务商账户限额。

`usage_records` 分开保存原始 API usage、输入估算、输出预留、预算计量依据、缓存命中、缺失/重试标记。失败且用量未知的尝试按输入和输出预留保守计量，不能当成实际账单。没有提供经核实的价格与账单时，现金费用为 `null`。终端每轮显示累计输入、输出、缓存命中和估算剩余。

恢复默认沿用保存的预算配置和已消耗量；修改命令行配置不会暗中重置预算。确实需要新预算时：

```powershell
plcforge resume SESSION_ID --workspace ./motor --replace-budget --total-token-budget 240000
```

替换保留已用量，并记录前后预算；后续实验应使用新运行 ID。`/retry` 也支持预算和上下文停止，但不会增加额度。

## 诊断与需求合同

词法位置映射保留注释和原始行列。缺少 `END_IF;`、`END_CASE;` 时，本地反馈给出文件、行列、代码、源片段、期望和实际符号。写文件成功与语法/接口检查分别展示；接口核对名字、类型、方向及 POU。重复诊断关联前后源码哈希、差异、历史 ID 和未解决位置。CODESYS 原始诊断、错误码和位置继续保留，语言、接口、子集限制与工具链错误分别标识。

本机 CODESYS SP22 Patch 4 实测允许省略上述两个结束符后的分号。本地可移植子集仍要求分号，并精确定位；报告保留两者差异，不把本地拒绝冒充原生编译失败。未知符号负对照由真实编译器检出。

题目合同包含原题、逐项暂定约定及来源、待确认问题、接口、扫描采样语义、版本和审核状态。`request_clarification` 接入运行流程，未确认问题会保存并暂停为 `needs_input`，不自动改成审核通过。P01 的阶段输出互斥、电平触发、每扫描最多一步、传感器保持高后的重启、提前确认不排队等约定均保持待确认。

## 独立评测和重跑

P01 原始试跑材料在合作者电脑上，本次未取得。仓库提供一套明确标为新建的验收题包：8 个开发用例 / 76 扫描，以及 387 个独立用例 / 1,604 扫描。它不是合作者 1,834 扫描原始评测的逐字复现。固定版本和哈希见 `evaluation/P01/manifest.json`。原有三模型结果没有被改写。

独立预期与报告必须位于模型工作区之外，模型工具不接受绝对路径或越界路径。此处是 Agent 工具的读写边界，不声称公开仓库中的测试是秘密。独立检查只在生成结束后执行，不反馈给模型修复。评测器要求匹配固定哈希、题目 ID 和合同哈希，报告保留失败、预算停止及原生编译结果。

```powershell
$oracle = Get-Content evaluation/P01/manifest.json -Raw | ConvertFrom-Json
plcforge run --task data/tasks/P01.json --workspace ./workspaces/P01-new `
  --holdout evaluation/P01/holdout.json --holdout-sha256 $oracle.sha256 `
  --evaluation-output ./evidence/P01-new.json
```

也可对已停止的运行单独执行 `plcforge evaluate`，提供同样参数以及 `--task`；可用 `--run-result` 绑定原始结果。源码改变后旧编译证据失效。

新增边沿、定时器、数组和多个功能块四类样例，见 `evaluation/extensions.json`。边沿/定时器有子集扫描验证；数组/自定义功能块组合明确标记子集不支持，并以真实 CODESYS 离线编译与代码生成补充。所有题型的 CODESYS 运行时与实体 PLC 验证仍为 `not_run`。

## 本地结果与验证入口

本次相同冻结 P01 输入的两次真实 DeepSeek 试跑均通过 8/8 开发测试、387/387 独立检查和真实 CODESYS 编译。0.1.4 为 7 次调用、150,206 输入 token；0.1.5 为 4 次调用、61,048 输入 token，下降约 59.4%。源码修改次数从 0 变为 1，编译次数均为 1。单模型各一次、服务商别名可更新，不能推广为普遍性能排名。

大结果压缩回放从 158,688 缩到 857 字符。该回放是独立本地测量；本次改进版真实试跑未触发压缩，token 改善同时包含工具摘要与宿主收尾等因素，不能全归因于压缩。

```powershell
python -m unittest discover -s tests -v
npm test
node npm/check-package.cjs
python scripts/verify_agent_native.py
```

Python 144 项和 Node 7 项测试通过；原生验收 8 个场景的预期均满足。Python 中预算停止注入场景使用明确标注的编译器测试替身；独立的原生验收和两次模型试跑才是真实编译证据。完整本地记录位于 `.plcforge/acceptance/` 和 `evidence/agent-v0.1.5/`，公开验收摘要附固定哈希与运行 ID。
