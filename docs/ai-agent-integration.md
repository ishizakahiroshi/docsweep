# AI エージェント統合ガイド

docsweep は **「全 AI エージェント対応」** を方針として、3 つの経路で AI から使えるよう設計されている:

| 経路 | 対象 AI | 仕組み |
|---|---|---|
| **MCP**（24 tool・書き込み系を含む） | Claude Code / Codex / Cursor / Continue 等、MCP 対応 AI 全般 | `python -m docsweep mcp` を MCP サーバーとして起動。自然言語起動の主役は `brief` / `cross` / `capture_extract` / `capture_save` だが、露出する tool はそれだけではない（下記一覧） |
| **CLI 直叩き** | あらゆる AI（Bash ツールがあれば動く） | `docsweep <command>` をシェル経由で実行。`--json` で構造化出力を得てパースする |
| **`/D` Skill**（Claude Code 専用） | Claude Code | `~/.claude/skills/D` 経由の薄いラッパー。MCP の主要 3 + CLI 直叩きをディスパッチ |

### MCP が露出する tool（v0.4.0 時点・24 個）

scan / list_projects / set_project_enabled / route_intent / doctor / day / brief / capture_extract / capture_save / cross / triage / apply / sweep / promote / index / summary / inject / eject / inject_global / eject_global / update_status / update_due / update_content / archive_done

**書き込み・設定変更を伴うもの**: `apply` / `sweep` / `promote` / `update_status` / `update_due` /
`update_content` / `archive_done`（md を書き換える）、`inject` / `eject`（プロジェクト設定を書き換える）、
`inject_global` / `eject_global` / `set_project_enabled`（ユーザーのグローバル設定を書き換える）。
MCP 登録は「読み取り専用の朝の入口」ではないので、AI に許可を渡す前にこの範囲を把握しておくこと。
tool を絞るオプションは現時点で無い（`docsweep mcp` に選択フラグは無い）。

> **設計の経緯**: 当初の方針は「自然言語起動の価値が一番高い "朝の入口" 系だけを MCP に出し、
> 残りは CLI 直叩き」だった。実装は段階的に tool が増えて 24 個になっており、上表はその実態に
> 合わせた記述（2026-08-25 の仕様点検で資料と実装のズレを修正）。当初方針の判断ログは
> `docs/local/plan_docsweep-wings_2026-06-29.md`。

---

## 自然言語マッピング表

AI に話しかけた典型発話と、その時 AI が選ぶべき経路・コマンド:

| ユーザー発話例 | 経路 | 実行 |
|---|---|---|
| 「今日の続きやって」「ブリーフして」「朝の状況」 | MCP | `brief()` |
| 「全プロジェクトの状況」「クロスで見せて」「どこから手をつける」 | MCP | `cross()` |
| 「これ plan にして」（直前会話貼付け）「キャプチャして」 | MCP | `capture_extract(text)` → `capture_save(drafts)` |
| 「整合チェックして」「この plan もう実装されてる？」 | CLI | `docsweep linkcheck --json` |
| 「廃止確認して」「状態遷移提案して」 | CLI | `docsweep auto-triage --suggest` |
| 「archive で似たやつ探して」「過去の類似 plan は」 | CLI | `docsweep resurrect --json` |
| 「関係性グラフ出して」「孤立してる plan は」 | CLI | `docsweep graph --json` |
| 「インデックス更新して」 | CLI | `docsweep index-sync` |
| 「全部作り直して」 | CLI | `docsweep index-rebuild` |
| 「監視しといて」「自動同期」 | CLI | `docsweep index-watch`（watchdog 必要） |
| 「この plan show して」「逆参照は」 | CLI | `docsweep show <path> --json` |
| 「これ俺が担当ね」 | CLI | `docsweep claim <path>` |
| 「pending 一覧」「保留は」 | CLI | `docsweep pending --json` |
| 「triage 出して」「残作業」 | MCP | `triage()` |

> 上表は「その発話でどちらを選ぶと速いか」の推奨であって、MCP に無いから CLI という意味ではない
> （`triage` / `apply` / `index` などは両方から使える）。

---

## 各 AI 向けセットアップ

### Claude Code（MCP + /D）

1. `pip install 'docsweep[mcp]'` で MCP extras を入れる
2. Claude Code の設定で MCP server を登録（`python -m docsweep mcp` を stdio で起動）
3. （任意）`~/.claude/skills/D/SKILL.md` を更新して新コマンドを通せるようにする（`docs/D-skill-update-proposal.md` の案文）

### Codex CLI / Cursor / Continue 等（MCP 対応 AI）

Claude Code と同じ。MCP server を register すれば自然言語で `brief` / `cross` / `capture_*` が呼べる。

それ以外のコマンド（`linkcheck` / `resurrect` / `graph` 等）は Bash ツールから `docsweep <command> --json` で叩く。

AI に渡す指示文の雛形:

```
docsweep は CLI ツールです。朝の入口は MCP の brief / cross / capture を使い、
それ以外（linkcheck / auto-triage / resurrect / graph / show / find / claim 等）は
Bash ツールから `docsweep <command> --json` で実行してください。
詳細: `docsweep --help` を見ること。
```

### MCP 非対応 AI（または最小構成）

すべて CLI 直叩きで完結する。`docsweep brief --json` が `brief` MCP tool と同じ JSON を返すので、AI は MCP 無しでも同等の体験を得られる。

---

## `--json` 出力スキーマ概要

すべての主要コマンドが `--json` をサポート。AI がパースしやすいよう以下を順守:

- ルートは常に dict（または list[dict]）
- 必須キー: コマンド固有（下記）
- 日付は ISO 8601、相対パスは POSIX 区切り
- 数値は明示的（カウントは `int`、スコアは `float`）

主要コマンドのスキーマ:

| コマンド | ルート構造 |
|---|---|
| `brief --json` | `{mode, generated_at, projects: [{project, today_pick, co_running, watchouts, yesterday_done, open_count, stale_count}]}` |
| `cross --json` | `{generated_at, project_filter, top_pick, runners_up, frozen_candidates, project_summaries, total_projects, total_open}` |
| `triage --json` | `{counts, items: [{path, rel, project, type, state, age_days, allowed_actions, ...}], needs_fix}` |
| `linkcheck --json` | `[{plan_path, plan_name, declared_files: [{path, exists, touches_since_plan, mentioned_in_commit}], progress_hint}]` |
| `auto-triage --suggest` | `{suggestions: [{path, project, current_state, proposed_action, proposed_to, reason, confidence}]}` |
| `resurrect --json` | `{mode, threshold, candidates: [{archive_path, archive_title, related_path, related_title, similarity, mode}]}` |
| `graph --json` | `{nodes: [{id, label, project, type, state, state_label, tags, isolated}], edges: [{source, target, resolved}]}` |
| `capture --json` | `{drafts: [{id, kind, title, body, suggested_filename, source_hint, project, tags}], saved: [path...]}` |
| `index-sync --json` | `{projects, files_total, files_added, files_updated, files_unchanged, files_deleted}` |

---

## トラブルシュート

- **MCP tool が AI に見えない**: MCP server が起動しているか確認 (`python -m docsweep mcp` を直接叩いて応答するか)
- **`brief` が「今日の 1 個」を出さない**: `docsweep index-sync` を 1 回走らせて索引を更新する
- **archive と現役の類似が検出されない**: `--no-embedding` を試して Jaccard モードの挙動を見る。embedding を使うなら `pip install 'docsweep[resurrect]'`
- **他 AI でも MCP を使いたい**: MCP は標準プロトコル。AI 側の docs を見て MCP server registration を設定する

詳細・運用ルールは README.md と `docs/conventions.md` を参照。
