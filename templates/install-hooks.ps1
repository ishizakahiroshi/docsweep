# docsweep pre-commit hook の opt-in 配置スクリプト（Windows PowerShell 用）。
#
# 何をするか:
#   templates/.githooks/docsweep-check.py を .git/hooks/pre-commit にコピーする。
#   docsweep を入れていないリポでも動くスタンドアロン hook。
#
# 使い方（リポルートで実行）:
#   pwsh templates/install-hooks.ps1
#
# 取り消したい場合:
#   Remove-Item .git/hooks/pre-commit

$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $here ".githooks/docsweep-check.py"

try {
    $gitDir = (git rev-parse --git-dir).Trim()
} catch {
    Write-Error "git リポジトリ内で実行してください"
    exit 1
}
if (-not (Test-Path $src)) {
    Write-Error "$src が見つかりません"
    exit 1
}

$hooksDir = Join-Path $gitDir "hooks"
if (-not (Test-Path $hooksDir)) {
    New-Item -ItemType Directory -Path $hooksDir | Out-Null
}
$dst = Join-Path $hooksDir "pre-commit"

if (Test-Path $dst) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backup = "$dst.bak.$stamp"
    Move-Item $dst $backup
    Write-Host "既存 pre-commit を退避: $backup"
}

Copy-Item $src $dst
Write-Host "docsweep pre-commit hook を配置しました: $dst"
Write-Host "（取り消す場合: Remove-Item $dst）"
