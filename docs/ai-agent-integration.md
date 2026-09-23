# AI エージェント統合ガイド

docsweep は **「全 AI エージェント対応」** を方針として、3 つの経路で AI から使えるよう設計されている:

| 経路 | 対象 AI | 仕組み |
|---|---|---|
| **MCP**（26 tool・書き込み系を含む） | Claude Code / Codex / Cursor / Continue 等、MCP 対応 AI 全般 | `python -m docsweep mcp` を MCP サーバーとして起動。自然言語起動の主役は `brief` / `cross` / `capture_extract` / `capture_save` だが、露出する tool はそれだけではない（下記一覧） |
| **CLI 直叩き** | あらゆる AI（Bash ツールがあれば動く） | `docsweep <command>` をシェル経由で実行。`--json` で構造化出力を得てパースする |
| **`/D` Skill**（Claude Code 専用） | Claude Code | `~/.claude/skills/D` 経由の薄いラッパー。MCP の主要 3 + CLI 直叩きをディスパッチ |

### MCP が露出する tool（release tracking 対応・26 個）

scan / find / set_target_release / list_projects / set_project_enabled / route_intent / doctor / day / brief / capture_extract / capture_save / cross / triage / apply / sweep / promote / index / summary / inject / eject / inject_global / eject_global / update_status / update_due / update_content / archive_done

**書き込み・設定変更を伴うもの**: `apply` / `sweep` / `promote` / `set_target_release` / `update_status` / `update_due` /
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

### 様子見期限の日数を今回だけ変える

通常は `.docsweep.yaml` の `due.default_offset_days` が使われます。特定の状態遷移だけ日数を
変える場合は設定ファイルを変更せず、次のように指定します。

```bash
python -m docsweep apply --path <plan-or-bugfix.md> \
  --action relabel --to watching --watching-days 5
```

MCP では `apply(path=..., action="relabel", to="watching", watching_days=5)` を使います。
同じ低レベル経路の `update_status(..., watching_days=5)` でも指定できます。`N` は 0 以上の
整数で、既に `watching` の文書を再指定した場合は既存の due を保護します。

### 卒業期限が来た様子見を片づける

`[様子見]` へ移した文書には、その日から既定 3 日後の卒業期限が `due` として入ります。
**`due` が来ただけでは何も起きません。** 昇格は明示操作でのみ実行されます。

```bash
python -m docsweep promote --due-expired --dry-run   # 対象を下見する
python -m docsweep promote --due-expired             # 昇格して archive へ移す
```

MCP では `promote(due_expired_only=True, dry_run=True)` で下見してから、`dry_run` を外して実行します
（CLI のフラグ名は `--due-expired`、MCP の引数名は `due_expired_only` で綴りが違う点に注意）。

AI がこれを扱うときの注意:

- **下見を先に出し、対象一覧をユーザーへ見せてから実行する。** `--due-expired` を付けない
  `promote` は様子見全件が対象になるので、下見と本実行で同じ絞り込みを使う
- 対象は「due 到来（当日を含む）」かつ `[様子見]` の文書だけ。`due` 無し・未来 due・不正 due・
  `docsweep_policy: never_archive` は動かない
- `sweep` は様子見を対象にしない。寝かせ中の文書を自動移送する経路は存在しない
- 期限切れを理由に `[廃止]` へ倒さない。廃止は人または AI の明示的な意思決定でのみ行う

### queue の中で文書を移す

作業文書をフォルダへ整理するときは、シェルの `mv` / `Move-Item` ではなく `docsweep mv` を使います。
移した文書を指す `docsweep_parent`・パス形式の `related`・本文中の repo 相対パスが一緒に書き換わります。

```bash
python -m docsweep mv docs/local/plan_x.md --to docs/local/app-a --dry-run --json   # 予定を下見する
python -m docsweep mv docs/local/plan_x.md --to docs/local/app-a --json             # 移す
```

archive の中でも同じフォルダ構成を保ちたい場合は、`.docsweep.yaml` に `archive_layout: mirror` を書きます。

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

## Git release tracking

release tracking は opt-in です。`release_tracking.mode: enabled` と
`archive_partition: release` を設定したプロジェクトだけ版別 archive を使います。
未設定・disabled・flat は既存の移送先を維持します。

新規 MD の `target_release` は、明示した `--target-release`、enabled な
project/global の `release_tracking.default_target` の順で解決します。split plan
では親と全子に同じ値が付きます。disabled では自動付与しません。未設定repoから人間の
TTYで `new` した場合は、enable、以後skip、cancelを初回だけ確認します。enable時は
default target、grouping、archive rootを保存してから生成します。非TTYでは質問しません。

`target_release` は Git tag の存在を求めない計画ラベルです。`released_in` は
`release close` が対象リポジトリで完全一致を確認した実在タグで、frontmatter には正確な
値を残します。タグが無い、pre-release が許可されていない、target が未設定または不一致、
watching / 未完了 / `never_archive` の文書は fail-closed で移送しません。

```bash
python -m docsweep new plan next-change --target-release v0.9.x
python -m docsweep target-release set --path docs/local/plan_existing.md --to v0.9.x
python -m docsweep find --target-release v0.9.x --json
python -m docsweep find --missing-target-release --json
python -m docsweep release close v0.9.1 --dry-run --json
python -m docsweep release close v0.9.1 --json
```

複数リポジトリの移行は manifest を先に作り、内容を確認してから明示適用します。manifest と
journal は本文を保存せず、リポジトリのパス、件数、設定差分、診断理由だけを持ちます。

```bash
python -m docsweep workspace migrate-release-tracking --root <workspace-root> --review \
  --manifest release-migration.json
python -m docsweep workspace migrate-release-tracking \
  --root <workspace-root> --apply-manifest release-migration.json
```

`--review` は人間の TTY で棚卸しを表示してから初回設定を確認し、最終action一覧への
apply確認後にだけ書き込み、事後結果を表示します。skip は project config の `mode: disabled` として保存され、
以後その repo へ質問しません。`q` または入力終了で cancel すると config、MD、manifest、
journal は変更しません。`--json`、`--auto`、CI、非 TTY、`--apply-manifest` は完全非対話で、
未設定なら `needs_review` / `needs_target` を構造化して返します。

`release close` と workspace migration は、Git tag・複数 repo・manifest/journal を扱う
明示 CLI 操作として公開しています。MCP にはこの一括操作の対話ラッパーを登録していません。
MCP は `scan` / `find` / `set_target_release` などの診断・明示指定面を使い、質問や暗黙の
一括適用を行わない契約です。

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
| `find --json` | `[{path, project, target_release, released_in, ...}]` |
| `release close --json` | `{tag, dry_run, movable, moved, watching, incomplete, target_mismatch, target_unset, never_archive, tag_missing, collision, failed}` |
| `workspace migrate-release-tracking --json` | `{mode, manifest: {migration_id, repositories, excluded, ...}, apply?}` |

---

## トラブルシュート

- **MCP tool が AI に見えない**: MCP server が起動しているか確認 (`python -m docsweep mcp` を直接叩いて応答するか)
- **`brief` が「今日の 1 個」を出さない**: `docsweep index-sync` を 1 回走らせて索引を更新する
- **archive と現役の類似が検出されない**: `--no-embedding` を試して Jaccard モードの挙動を見る。embedding を使うなら `pip install 'docsweep[resurrect]'`
- **他 AI でも MCP を使いたい**: MCP は標準プロトコル。AI 側の docs を見て MCP server registration を設定する

詳細・運用ルールは README.md と `docs/conventions.md` を参照。
