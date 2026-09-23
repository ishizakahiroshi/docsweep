# docsweep pre-commit hook の opt-in 配置スクリプト（Windows PowerShell 用）。
#
# 何をするか:
#   templates/.githooks/docsweep-check.py を .git/hooks/pre-commit にコピーする。
#   hook が読む文言辞書 docsweep-check.i18n.json も同じ .git/hooks/ へコピーする。
#   docsweep を入れていないリポでも動くスタンドアロン hook。
#
# 使い方（リポルートで実行）:
#   pwsh templates/install-hooks.ps1
#
# 取り消したい場合:
#   Remove-Item .git/hooks/pre-commit, .git/hooks/docsweep-check.i18n.json

$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$src = Join-Path $here ".githooks/docsweep-check.py"
$i18nSrc = Join-Path $here ".githooks/docsweep-check.i18n.json"

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
if (-not (Test-Path $i18nSrc)) {
    Write-Error "$i18nSrc が見つかりません"
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
# hook は隣の辞書を読む（無いと 1 行出して止まる）。辞書は hook の版に合わせるだけなので上書きでよい。
Copy-Item $i18nSrc (Join-Path $hooksDir "docsweep-check.i18n.json") -Force
Write-Host "docsweep pre-commit hook を配置しました: $dst"
Write-Host "（取り消す場合: Remove-Item $dst）"
