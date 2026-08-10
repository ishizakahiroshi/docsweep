# Third-Party Notices

docsweep 自体は MIT License で配布されています（リポジトリ直下 `LICENSE` 参照）。
本ファイルは **配布物に bundle されているサードパーティ・ソフトウェア** の出典と
ライセンスを記録するためのものです。

> 注: bundle 対象のうち **cytoscape.js は MIT License** であり、著作権表記とライセンス全文の
> 保持が配布条件です（下記 "Bundled" に転載）。htmx は 0BSD のため法的には不要ですが、
> 透明性・利用者への情報提供の観点から同じ形式で記録しています。
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

### cytoscape.js 3.30.0

- 用途: Web UI の graph ページのネットワーク可視化
- 同梱パス: `docsweep/server/static/cytoscape.min.js`
- 出典: https://github.com/cytoscape/cytoscape.js
- 取得元: `https://unpkg.com/cytoscape@3.30.0/dist/cytoscape.min.js`（2026-08-10 取得）
- SHA-256: `a298b253e4b9b08bd0b2fe222ad67b2b9f42d057d7c17f7a050512079c46fddd`
- ライセンス: **MIT License**
- 著作権表記: `Copyright (c) 2016-2024, The Cytoscape Consortium.`（原 LICENSE 全文を下記に記載）

```
Copyright (c) 2016-2024, The Cytoscape Consortium.

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

> 2026-08-10（v0.4.0）より前は CDN（unpkg）から実行時取得していました。オフライン完結と
> 外部への利用状況の送信回避のため同梱へ切り替えています。

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
