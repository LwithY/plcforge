# npm installation

Windows 10/11 with Node.js 20+:

```powershell
npm install -g plcforge
plcforge
```

The global npm command runs in the current folder. Installation reuses Python 3.11+ when available; otherwise it downloads a pinned official Windows embeddable runtime, verifies SHA-256, and stores it under `~/.plcforge/runtime`. No pip dependencies are needed. The initial download needs Internet access. Windows x64 is the tested platform; the runtime manifest also supports Windows ARM64. CODESYS itself is a separately installed Windows application.

Editable configuration is stored outside npm's package directory:

- `~/.plcforge/settings.json`: model URL, model ID and your own API credential.
- `~/.plcforge/plcforge.json`: runtime limits and machine-specific CODESYS configuration.
- `~/.plcforge/codesys/`: generated compiler template and verification receipts.

`PLCFORGE_HOME` overrides this directory. Updates preserve existing configuration. Project-specific `.plcforge/settings.json` or `--settings PATH` can select another model. Run `/settings reload` after editing a model configuration; this starts a new session. API credentials are not provided by the package.

After installing CODESYS with the Scripting add-on, the installer detects standard installation folders and prepares a copy of the vendor's template. It performs a real offline compiler probe before reporting `ready_verified`. It never logs in to a PLC, downloads PLC code, or starts equipment.

If CODESYS is installed later or in a custom location:

```powershell
plcforge setup --codesys 'E:\CODESYS\YourInstalledVersion'
plcforge doctor
```

This command prepares the native compiler and preserves model configuration. An unavailable compiler leaves ordinary chat and code generation usable, with compilation explicitly pending. The adapter has been tested on CODESYS 3.5 SP22 Patch 4; other installations are verified locally by the probe.

```powershell
plcforge settings
plcforge settings init
plcforge run -p 'Implement a PLC motor latch with stop priority'
plcforge --settings 'D:\configs\my-model.json'
```

The package uses OpenAI-compatible Chat Completions/SSE and tool calling. Each natural-language request saves a numbered JSON transcript inside the current project's `.plcforge/sessions/` directory. Public progress is shown in the terminal; private reasoning is omitted from the export and credentials are redacted.

If npm scripts were disabled, the first invocation also performs lazy setup. `PLCFORGE_PYTHON` can select a Python executable. `PLCFORGE_SKIP_CODESYS_SETUP=1` skips native compiler preparation during installation; run `plcforge setup` later. No PATH edits beyond npm's normal global command installation are performed. npm's global bin directory must already be available in the terminal PATH.

Uninstalling the npm package keeps personal settings and project files. A newer release can be installed with `npm install -g plcforge@latest`.
