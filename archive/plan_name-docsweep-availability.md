# [完了] プロジェクト名「docsweep」採否・被り調査

> 最終更新: 2026-06-13(土) 12:19:21

## context配分

| C | 内容 | 種別 |
|---|---|---|
| C1 | 候補名のブレストと PyPI/npm 一次被りチェック | plan |
| C2 | 本命「docsweep」の広域被り調査（GitHub/Win アプリ/Web/ドメイン）と最終判定 | plan |

---

## 概要

`ai-docs-sweep` のリポジトリ名を、よりキャッチーで採用したくなる名前へ改称する検討。
候補をブレストし PyPI（`pip install` 名前空間が最重要）・npm・GitHub・一般 Web・ドメインで
被りを調査した結果、**`docsweep` に決定**。

ツール実体: AI が量産する plan/bugfix/pending の Markdown を自動アーカイブ・陳腐化検出・
横断 INDEX 化する片付け CLI。

---

## C1 候補ブレストと一次チェック（2026-06-13）

PyPI / npm の空き状況（404=空き / 200=使用中）:

| 名前 | PyPI | npm | 備考 |
|---|---|---|---|
| **docsweep** ★採用 | 空き | 空き | 既存名の "sweep" ブランド継承・`ai-` 接頭辞を除去 |
| mdsweep | 空き | 空き | 対抗（さらに短い） |
| mdjanitor | 空き | 空き | キャラ性で候補 |
| markprune / mdprune | 空き | 空き | 庭師メタファー |
| docsift | 空き | 空き | triage 寄り |
| mdgroom / sweepmd / mdcurator / mdkeeper | 空き | 空き | 補欠 |
| mdtidy / markdex / archivist | 使用中 | — | PyPI 使用中で除外 |
| plandex | 空き | 使用中 | 有名 AI コーディングツールと衝突のため除外 |

---

## C2 「docsweep」広域被り調査と最終判定

### クリーン（被りなし）

- PyPI `docsweep` … 空き（`pip install docsweep` 取得可）
- npm `docsweep` … 空き
- GitHub リポジトリ `docsweep` … 同名 0 件
- GitHub ユーザー/Org `github.com/docsweep` … 404（未取得＝空き）
- Windows アプリ / 一般ソフト … 同名アプリ・ツールなし（Microsoft Store 等もヒットなし）

### 注意点（致命的ではない）

1. **「Docsweeper」が存在**（最重要）
   Python 製リンター。git 履歴から古い docstring を検出するツールで、名前が 1 文字違い
   （`docsweep` + `er`）かつ「Python 製・古い記述を検出する開発ツール」というコンセプトも近い。
   混同回避のため README 冒頭で差別化を 1 文入れる方針。
   参照: https://docsweeper.readthedocs.io/en/stable/

2. **ドメイン docsweep.com は取得済み**
   煙突掃除業者「Dr. Sweep」（docsweep.co.uk / facebook.com/docsweep）系が保有（AWS ホスト）。
   ソフト系ではないため実害薄。`.com` は取得不可。
   docsweep.dev / .io / .app / .org は DNS 未解決＝取得可能。開発ツールなら **docsweep.dev** が筋が良い。

### 最終判定: 採用（GO）

唯一の近接が「Docsweeper」のため、そこだけ README で差別化すれば十分。

---

## 推奨フォローアップ（名前確保）

- PyPI / npm に `docsweep` を placeholder で予約 publish
- GitHub ユーザー名 or Org `docsweep` を確保（リポジトリ名と一致）
- ドメイン `docsweep.dev` を取得（`.com` は煙突業者保有のため回避）
- README に Docsweeper との差別化文を 1 文追加

## 関連

- 製品要件: [plan_v0.1.0-product-requirements.md](plan_v0.1.0-product-requirements.md)
