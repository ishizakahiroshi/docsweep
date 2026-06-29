# `docsweep export --okf` の出力フォーマット

「docsweep を抜けても md が腐らない」を実演するためのエクスポートコマンドです。
スキャン範囲内の plan / bugfix / pending を frontmatter ごとそのまま取り出し、
OKF 互換語彙との対応表を `okf-manifest.json` として同梱した zip を出力します。

## 使い方

```bash
# 既定: カレントの ./docsweep-okf-<date>.zip に出力
python -m docsweep export --okf

# 出力先を明示
python -m docsweep export --okf --out /tmp/snapshot.zip

# 特定プロジェクトだけ
python -m docsweep export --okf --project many-ai-cli

# archive/ 配下も含める
python -m docsweep export --okf --include-archive

# 機械可読 JSON で結果を受ける
python -m docsweep export --okf --json
```

## zip の構造

```
docsweep-okf-2026-06-29.zip
├─ okf-manifest.json           # OKF 互換語彙との対応表（後述）
├─ <project_a>/
│  └─ docs/local/
│     ├─ plan_xxx.md          # frontmatter + 本文をバイトレベルで温存
│     └─ bugfix_yyy_2026-06-29.md
├─ <project_b>/
│  └─ docs/
│     └─ pending_zzz.md
└─ _archive/                   # --include-archive 指定時のみ
   └─ <project_a>/archive/
      └─ plan_old.md
```

- 各 md は **プロジェクト境界からの相対パス**で配置（リポ名と階層が一目で分かる形）。
- 同一エントリ名が衝突したら `__1` / `__2` のサフィックスで一意化されます。
- 本文・frontmatter・H1 ラベルはバイトレベルで温存されます（docsweep 側で変換しない）。

## `okf-manifest.json` のスキーマ

```jsonc
{
  "format": "okf",                        // 固定値
  "okf_version": "0.1",                   // docsweep が準拠する OKF 仕様の版
  "docsweep_version": "0.1.0",            // 生成時の docsweep バージョン
  "generated_at": "2026-06-29T12:34:56+09:00",
  "include_archive": false,
  "type_vocabulary": {
    "plan":    { "okf_equivalent": "plan",     "description": "..." },
    "bugfix":  { "okf_equivalent": "incident", "description": "..." },
    "pending": { "okf_equivalent": "deferred", "description": "..." }
  },
  "status_vocabulary": {
    "planned": "draft", "in-progress": "active", "watching": "active",
    "done": "done", "discarded": "discarded", "pending": "deferred"
  },
  "review_status_vocabulary": ["draft", "review", "published"],
  "file_count": 42,
  "files": [
    {
      "path": "many-ai-cli/docs/local/plan_xxx.md",
      "type": "plan",
      "status": "draft",
      "title": "認証フローのリファクタ",
      "tags": ["auth", "backend"],
      "owner": "ishizakahiroshi",
      "review_status": "draft",
      "related": ["plan_yyy.md"],
      "last_reviewed": "2026-06-29"
    }
    // ...
  ]
}
```

`status` は **OKF 互換語彙に変換した値**が入ります（docsweep 内部 state key ではなく
manifest 側で OKF 寄せに丸めた値）。元の docsweep 表記が必要なら md 本体の frontmatter
を見てください（こちらは温存されています）。

## 互換性

- zip は標準形式（PKZIP）。OS 標準のアーカイバ・`unzip` / `tar` で展開できます。
- `okf-manifest.json` は UTF-8 JSON。エディタで開けます。
- 出力された md 群は **docsweep を入れていない別プロジェクトに展開しても**、
  frontmatter の `type` / `status` / `related` がそのまま OKF 互換で読めます。

## 何のために使うか

1. **docsweep を抜ける移行**: docsweep をやめても md は OKF 形式として残り、別ツールで
   読み続けられる。「ベンダーロックインしないこと」の実証材料。
2. **チーム間共有**: 1 つのプロジェクトの作業ログを別チームへ渡す際、frontmatter 込みで
   1 ファイル送れば全部入る。
3. **バックアップ**: 定期的に export しておけば「全 md スナップショット」が手に入る。

## やらないこと（不変条件）

- 本文の自動編集（frontmatter 補完・H1 ラベル整形等は **しない**。`migrate-frontmatter`
  との責務分離）。
- AI による要約・タグ推定（docsweep 自身は AI API を叩かない）。
- 元 md の削除（export は **読み取り専用**）。
