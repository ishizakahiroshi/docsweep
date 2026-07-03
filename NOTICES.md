# Third-Party Notices

docsweep 自体は MIT License で配布されています（リポジトリ直下 `LICENSE` 参照）。
本ファイルは **配布物に bundle されているサードパーティ・ソフトウェア** の出典と
ライセンスを記録するためのものです。

> 注: ここに挙げる依存はいずれも **法的に著作権表記が必須なライセンスではありません**
> （bundle 対象は現状 0BSD のみ）。それでも透明性・将来 BSD/MIT 等を bundle する場合の
> 足場・利用者への情報提供の観点から記録を残しています。
>
> **pip install で別途解決される依存**（PyYAML / FastAPI / Jinja2 / mcp 等）は
> bundle ではないため本ファイル対象外です。各依存パッケージは PyPI 上で自身の
> LICENSE を同梱して配布されており、ユーザーは pip 経由で個別に受け取ります。

## Bundled

### htmx 1.9.12

- 用途: Web UI の HTML over the wire (HTMX) ライブラリ
- 同梱パス: `docsweep/server/static/htmx.min.js`
- 出典: https://github.com/bigskysoftware/htmx
- ライセンス: **BSD Zero Clause License (0BSD)**
- 著作権表記: 0BSD のため不要（参考までに原 LICENSE 全文を下記に記載）

```
# Zero-Clause BSD License

Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby granted.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
```

## Loaded from CDN (not bundled)

### cytoscape.js 3.30.0

- 用途: Web UI の graph ページのネットワーク可視化
- 読込元: `https://unpkg.com/cytoscape@3.30.0/dist/cytoscape.min.js`（実行時に CDN から取得・配布物には含まれない）
- 出典: https://github.com/cytoscape/cytoscape.js
- ライセンス: **MIT License**（同梱していないため転載義務はないが、利用の透明性のため記録）

## Runtime dependencies (not bundled — pulled by pip)

参考。これらは wheel に含まれず、ユーザーの `pip install` 時に PyPI から個別に取得されます。
各パッケージは自身の LICENSE を同梱して配布されるため、本ファイルでの転載は不要です。

- **PyYAML** — MIT License — 設定ファイル読み書き
- **fastapi** / **uvicorn** / **jinja2** / **markdown** / **python-multipart** / **nh3** — Web UI（`docsweep[web]`）
- **questionary** — 対話レビュー（`docsweep[review]`）
- **mcp** — MCP stdio サーバー（`docsweep[mcp]`）

## メンテナンス方針

- 新しい静的アセット（JS / CSS / フォント / 画像のうち外部由来のもの）を `docsweep/server/static/` 等に bundle した場合は、**本ファイルの "Bundled" セクションに追記**する
- 「bundle 追加 → NOTICES 追記」を忘れない仕組みは `pypi-publish` スキルの前提チェックに記載
- pip 依存（`pyproject.toml` の dependencies / optional-dependencies）の増減は本ファイル対象外
