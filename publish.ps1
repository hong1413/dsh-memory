<#
.SYNOPSIS
  把本目录发布到 GitHub（首次推送 / 后续推送都用它）。

.DESCRIPTION
  自动定位 git：先找 PATH，再找本机便携版 <workspace>\_tools\git\cmd\git.exe。
  首次推送会弹出 GitHub 登录（Git Credential Manager），登录一次即可；之后静默推送。

  若本机装有网络加速器（如 Watt Toolkit / Steam++）且把 github.com 指到 127.0.0.1，
  git 的 schannel 会报 CRYPT_E_NO_REVOCATION_CHECK。脚本默认改用 OpenSSL 后端并
  关闭证书校验（`http.sslVerify=false`）。不信任该代理时删掉这两个参数。

.EXAMPLE
  .\publish.ps1
  .\publish.ps1 -RepoUrl https://github.com/hong1413/-.git -Message "feat: v1.2.0"
#>
param(
  [string]$RepoUrl = "https://github.com/hong1413/-.git",
  [string]$Message = "chore: release",
  [string]$Branch = "main",
  [switch]$StrictTls
)

$ErrorActionPreference = "Stop"

function Resolve-Git {
  $cmd = Get-Command git -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  $candidates = @(
    (Join-Path (Split-Path $PSScriptRoot -Parent) "_tools\git\cmd\git.exe"),
    (Join-Path (Split-Path (Split-Path $PSScriptRoot -Parent) -Parent) "_tools\git\cmd\git.exe"),
    "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe",
    "C:\Program Files\Git\cmd\git.exe"
  )
  foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
  throw "未找到 git。装它：winget install --id Git.Git -e；或用 PUBLISH.md 的网页上传方式。"
}

$git = Resolve-Git
Write-Host "git: $git" -ForegroundColor DarkGray

$sslArgs = @()
if (-not $StrictTls) {
  $sslArgs = @("-c", "http.sslBackend=openssl", "-c", "http.sslVerify=false")
}

Push-Location $PSScriptRoot
try {
  if (-not (Test-Path ".git")) {
    & $git init -b $Branch | Out-Null
  }
  & $git config user.name "hong1413"
  & $git config user.email "hong1413@users.noreply.github.com"
  & $git config core.autocrlf false
  & $git add -A
  $pending = & $git status --porcelain
  if ($pending) {
    & $git commit -m $Message | Out-Null
  } else {
    Write-Host "没有未提交的改动" -ForegroundColor DarkGray
  }
  $remotes = & $git remote
  if ($remotes -notcontains "origin") {
    & $git remote add origin $RepoUrl
  } else {
    & $git remote set-url origin $RepoUrl
  }
  Write-Host "推送到 $RepoUrl ($Branch)…（首次会弹 GitHub 登录）" -ForegroundColor Cyan
  & $git -C $PSScriptRoot @sslArgs -c credential.helper=manager push -u origin $Branch
  if ($LASTEXITCODE -ne 0) { throw "推送失败（exit $LASTEXITCODE）" }
  Write-Host "已推送: $RepoUrl ($Branch)" -ForegroundColor Green
} finally {
  Pop-Location
}
