# `docsweep export --okf` の出力フォーマット

`export --okf` は、docsweep の管理対象文書を OKF v0.2 Bundle として持ち出すための
read-only export です。正本は [OKF v0.2 公式仕様](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
であり、`okf-manifest.json` は OKF 標準ではなく docsweep 固有の補助情報です。

## 使い方

```bash
# 既定: ./docsweep-okf-<date>.zip
python -m docsweep export --okf

# 出力先・対象を指定
python -m docsweep export --okf --out /tmp/snapshot.zip
python -m docsweep export --okf --project many-ai-cli
python -m docsweep export --okf --include-archive

# 結果を JSON で受ける
python -m docsweep export --okf --json

# profile を選ぶ。指定しなければ同梱 profile をオフラインで使用
python -m docsweep export --okf --okf-version 0.2
python -m docsweep export --okf --okf-profile ./okf-profile.json
python -m docsweep export --okf \
  --okf-profile https://raw.githubusercontent.com/org/repo/<commit>/okf.json \
  --okf-profile-sha256 <sha256>
```

URL profile はオプション指定時だけ取得します。CI では commit 固定 URL または SHA-256 固定を
使ってください。取得失敗時に別 profile へ黙って切り替えることはありません。

## Bundle の構造

```text
docsweep-okf-2026-08-09.zip
├─ index.md                    # Bundle root の予約 index
├─ okf-manifest.json            # docsweep 固有の補助 manifest
├─ <project_a>/
│  └─ docs/local/
│     ├─ plan_xxx.md
│     └─ bugfix_yyy_2026-08-09.md
├─ <project_b>/
│  └─ docs/pending_zzz.md
└─ _archive/                    # --include-archive 指定時のみ
   └─ <project_a>/archive/plan_old.md
```

- root `index.md` は `okf_version` だけを持つ frontmatter と、収録 concept への Markdown link
  一覧を持ちます。
- root 以外の `index.md` と `log.md` は OKF の予約形式として扱われます。
- 通常の `.md` は parse 可能な YAML frontmatter と空でない `type` を持つようにします。
- 旧形式や frontmatter 無しの入力は、原本を変更せず Bundle 内のコピーだけを最小限正規化します。
  `status` は profile の lifecycle、作業状態は `docsweep_state` へ分けます。
- `sources` / `generated` / `verified` / `stale_after` など未知の追加キーはコピー内でも保持します。
- 同一 entry 名が衝突した場合は `__1` / `__2` の suffix で Bundle 内のパスを一意化します。

## `okf-manifest.json`

```jsonc
{
  "format": "okf",
  "okf_version": "0.2",
  "okf_profile": {
    "spec_version": "0.2",
    "source": "bundled:0.2",
    "sha256": "..."
  },
  "docsweep_version": "0.3.1",
  "generated_at": "2026-08-09T12:34:56+09:00",
  "include_archive": false,
  "status_vocabulary": {
    "planned": "draft",
    "in-progress": "draft",
    "watching": "draft",
    "done": "stable",
    "discarded": "deprecated",
    "pending": "draft"
  },
  "file_count": 42,
  "files": [
    {
      "path": "many-ai-cli/docs/local/plan_xxx.md",
      "type": "plan",
      "status": "draft",
      "docsweep_state": "planned",
      "title": "認証フローのリファクタ",
      "normalized": true,
      "tags": ["auth", "backend"],
      "owner": "ishizakahiroshi",
      "review_status": "draft",
      "related": ["plan_yyy.md"],
      "docsweep_parent": "docs/local/plan_parent.md",
      "last_reviewed": "2026-08-09"
    }
  ]
}
```

`status` は profile に定義された OKF lifecycle です。`docsweep_state` が docsweep 内部の
作業状態です。`normalized` は入力 frontmatter を Bundle 内で補完・分離したかを示し、
`true` でも元ファイルを書き換えたことを意味しません。

## read-only 検査

任意の Bundle（ディレクトリまたは zip）は次で検査できます。

```bash
python -m docsweep okf-check ./bundle --json
```

error は必須 frontmatter、予約ファイル形式、壊れた YAML、UTF-8 など構造上の不適合です。
unknown type、unknown field、欠けた optional field、broken link は OKF の許容範囲を尊重して
warning または受理とし、Bundle 全体を即 reject しません。検査はファイルを変更せず、終了コードは
error があると `1`、profile / path を読めない場合は `2`、error が無ければ `0` です。

## 互換性と用途

- zip は通常の PKZIP で、OS 標準アーカイバで展開できます。
- Bundle 内の通常 Markdown は docsweep が無い別ツールでも `type` / `status` を読めます。
- docsweep をやめる移行、チーム共有、スナップショット保存のいずれにも使えます。
- 元 md の削除、本文の AI 要約、根拠フィールドの推測生成は行いません。
