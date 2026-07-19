---
schemaVersion: 1
color: "#7a9e2f"
initials: "do"
cat:
  ja: "CLI / Python"
  en: "CLI / Python"
tagline:
  ja: "AI が量産する作業ログ Markdown を、腐らせず自動で片付ける。"
  en: "Keep the work-log Markdown your AI agents generate from rotting."
short:
  ja: "AI が量産する plan / bugfix の Markdown を、腐らせず自動で片付けるクロスプラットフォーム CLI。"
  en: "A cross-platform CLI that keeps the plan/bugfix Markdown logs AI agents generate from rotting."
tech: ["Python", "CLI", "Web UI", "SQLite", "MCP"]
store: null
live: null
guide: null
featured: true
features:
  - icon: "⌦"
    title: { ja: "完了を自動アーカイブ", en: "Auto-archive finished docs" }
    desc:  { ja: "H1 ステータスラベルや frontmatter を読み、完了した md を archive/ へ移送。", en: "Reads status labels/frontmatter and moves finished docs to archive/." }
  - icon: "⚑"
    title: { ja: "陳腐化を可視化", en: "Surface stale docs" }
    desc:  { ja: "古くなったドキュメントを「要判断」フラグで検出して一覧化。", en: "Flags outdated docs as “needs review.”" }
  - icon: "▤"
    title: { ja: "横断 INDEX", en: "Cross-project index" }
    desc:  { ja: "複数プロジェクトの plan / bugfix / pending を 1 画面で一望。", en: "See plan/bugfix/pending across all projects in one view." }
---
## ja

Claude Code や Codex などの AI コーディングツールが生成する plan_*.md / bugfix_*.md / pending_*.md は、放っておくと溜まり続けて陳腐化します。docsweep は H1 ステータスラベル（[完了] / [計画] 等）や frontmatter を機械的に読み取り、完了したものを各プロジェクトの archive/ へ自動移送し、古くなったものを「要判断」フラグで可視化。複数プロジェクトを横断 INDEX で一望できます。

## en

AI coding tools like Claude Code and Codex generate plan/bugfix/pending Markdown files that pile up and go stale. docsweep reads H1 status labels and frontmatter, auto-archives finished docs, flags stale ones for review, and gives a cross-project index.
