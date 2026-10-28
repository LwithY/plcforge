param([string]$InstallDir = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectCommand = Join-Path $projectRoot 'plcforge.cmd'
if (-not (Test-Path -LiteralPath $projectCommand -PathType Leaf)) {
    throw 'Project launcher is missing.'
}
if (-not $InstallDir) { $InstallDir = Join-Path $env:USERPROFILE '.local\bin' }
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$globalCommand = Join-Path $InstallDir 'plcforge.cmd'
$marker = 'rem PLCForge global launcher v1'
if (Test-Path -LiteralPath $globalCommand) {
    $existingLauncher = Get-Content -LiteralPath $globalCommand -Raw
    if (-not $existingLauncher.Contains($marker)) {
        throw "An unrelated command already exists at $globalCommand. It was not changed."
    }
}
# Validate the interpreter before changing the user command installation.
& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'launch.ps1') --version
if ($LASTEXITCODE -ne 0) { throw 'Project launcher validation failed.' }
New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
# Transfer to the project batch file without changing the caller's directory.
# No CALL means arguments are not expanded a second time by cmd.exe.
$launcherText = "@echo off`r`n$marker`r`n" + ('"{0}" %*' -f $projectCommand) + "`r`n"
[IO.File]::WriteAllText($globalCommand, $launcherText, [Text.Encoding]::Default)
$userPathBefore = [Environment]::GetEnvironmentVariable('Path','User')
$userPathParts = @($userPathBefore -split ';' | Where-Object { $_ })
$hasUserEntry = @($userPathParts | Where-Object {
    [Environment]::ExpandEnvironmentVariables($_).TrimEnd('\') -ieq $InstallDir.TrimEnd('\')
}).Count -gt 0
if (-not $hasUserEntry) {
    $newUserPath = if ([string]::IsNullOrEmpty($userPathBefore)) { $InstallDir } else { $userPathBefore.TrimEnd(';') + ';' + $InstallDir }
    [Environment]::SetEnvironmentVariable('Path',$newUserPath,'User')
    # Ask Explorer to supply the new environment to future terminal processes.
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class PLCForgeEnvironmentNotify {
    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam,
        string lParam, uint flags, uint timeout, out UIntPtr result);
}
'@
    $notificationResult = [UIntPtr]::Zero
    [void][PLCForgeEnvironmentNotify]::SendMessageTimeout([IntPtr]0xffff,0x1A,[UIntPtr]::Zero,'Environment',2,3000,[ref]$notificationResult)
}
if (-not (@($env:Path -split ';' | Where-Object { $_.TrimEnd('\') -ieq $InstallDir.TrimEnd('\') }).Count)) {
    $env:Path = $InstallDir + ';' + $env:Path
}
$installation = [ordered]@{
    command = 'plcforge'
    launcher = $globalCommand
    project = $projectRoot
    user_path_entry_added = (-not $hasUserEntry)
    existing_user_path_preserved = $true
    default_workspace = 'current directory'
}
$recordDirectory = Join-Path $projectRoot '.plcforge'
New-Item -ItemType Directory -Path $recordDirectory -Force | Out-Null
$installation | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $recordDirectory 'global-install.json') -Encoding UTF8
$installation | ConvertTo-Json
