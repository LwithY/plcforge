# 自定义 LLM 配置（0.1.3）

编辑 `/settings` 显示的 `settings.json`，填写服务地址、模型 ID 和密钥，重启 `plcforge` 即可生效。npm 安装的默认文件在 `~/.plcforge/settings.json`；源码运行也可使用安装目录文件。已打开的交互界面可输入 `/settings reload`。默认配置沿用 ModelScope 模型，可从环境变量读取 `MODELSCOPE_API_KEY`。

## 最简单的配置

```json
{
  "base_url": "https://你的服务地址/v1",
  "model": "供应商提供的模型ID",
  "api_key": "在这里填写你的API密钥"
}
```

使用合法 JSON，不加注释或末尾多余的逗号。`base_url` 填 OpenAI 兼容 API 基础地址；如果粘贴完整的 `/chat/completions` 地址，程序会自动去掉该后缀，调用时再补齐。支持 HTTPS 和本地 HTTP。模型 ID 使用供应商提供的准确代码。

支持 OpenAI Chat Completions 的流式响应和工具调用协议；其他原生协议需要兼容网关。完整 PLC 编程流程需要所选模型支持工具调用。

## 环境变量与本地服务

不直接填写密钥时：

```json
{
  "base_url": "https://api-inference.modelscope.cn/v1",
  "model": "deepseek-ai/DeepSeek-V4.1-Flash-1017",
  "api_key": "",
  "api_key_env": "MY_LLM_API_KEY"
}
```

PowerShell 中设置变量再启动；npm 用户也可写入 `~/.plcforge/.env`，源码用户可写入安装目录 `.env`：

```powershell
$env:MY_LLM_API_KEY = '填写你的密钥'
plcforge
```

非空 `api_key` 优先；否则读取 `api_key_env`。省略后者时默认读取 `PLCFORGE_API_KEY`。切换供应商时，同时填写该供应商的密钥，或修改对应的变量名。

不需要认证的本地服务可配置为：

```json
{
  "base_url": "http://127.0.0.1:11434/v1",
  "model": "你本地实际提供的模型ID",
  "api_key": "",
  "api_key_env": ""
}
```

此时不发送 Authorization 请求头。直接填写的非空密钥至少 8 个字符；无认证服务使用上面的空值设置。

## 文件位置与优先级

默认选择第一份存在的文件，不合并不同文件的凭据：

1. 当前工程 `.plcforge/settings.json`。
2. 用户目录 `~/.plcforge/settings.json`。
3. PLCForge 安装目录 `settings.json`。

npm 安装支持用 `PLCFORGE_HOME` 改变用户配置目录。

工程目录默认为终端当前文件夹；设置 `--workspace` 时使用该目录。显式指定文件则只使用该文件：

```powershell
plcforge --settings 'D:\我的配置\settings.json'
plcforge run --settings 'D:\我的配置\settings.json' -p '解释PLC扫描周期'
```

创建本工程模板、查看当前实际配置：

```powershell
plcforge settings init
plcforge settings
```

`init` 在工程创建 `.plcforge/settings.json`，已有文件保持原样。也可用 `plcforge settings init --settings 'D:\我的配置\settings.json'` 创建指定模板。模板只复制非敏感设置，不复制已有密钥。

设置文件覆盖旧 `plcforge.json` 中的模型默认值；`PLCFORGE_BASE_URL`、`PLCFORGE_MODEL` 环境变量进一步覆盖文件；命令行 `--model` 优先级最高。环境变量将 URL 改为其他端点时，清除原直接密钥，改用 `PLCFORGE_API_KEY`。CODESYS 配置继续由原 `plcforge.json` 管理。

## 交互命令

- `/settings`：显示选中的文件、URL、模型和密钥是否存在，不打印密钥。
- `/settings reload`：重新读取文件，建立新会话；旧会话和任务记录保留。
- `/model`：显示当前模型 ID 和接口地址。
- `/model 模型ID`：在当前接口上临时换模型，建立新会话，不修改文件。

重新加载会重新应用启动时的 `--model` 等命令行覆盖值。格式或参数错误时会继续使用原配置；缺少密钥时，实际调用前会报告缺少哪个变量。

可选网络参数：`max_output_tokens`、`timeout_seconds`、`max_retries`、`retry_base_seconds`。未知字段和非法地址在本地报错。JSON 错误只报告行号、列号，不回显可能含密钥的原文。

## 记录与验证

直接填写和环境变量中的密钥均纳入脱敏，覆盖终端、每任务 JSON、错误和状态输出。模型文件工具不能读取 `settings.json`。请求记录保留实际 URL 和模型。

用户 `settings.json` 被 Git 忽略；源码包只生成干净默认文件，并提供 `settings.example.json`。四组课程实验继续由 `experiments/matrix.json` 单独指定各组合的模型和凭据变量。

`evidence/settings.json` 记录验证结果：自动化测试用不同模拟端点核对 URL、模型和认证头；真实 ModelScope 调用验证自定义文件的读取及接口调用。模拟测试不代表其他供应商的真实联调。
