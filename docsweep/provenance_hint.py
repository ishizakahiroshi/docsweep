"""provenance が有効なのに AI 情報が既定値のままのときの案内。

``docsweep new`` も ``docsweep provenance start`` も、``--ai-*`` / ``--agent`` 等を
省くと **黙って** ``unknown`` を台帳へ書く。あとから frontmatter だけ直すと
``provenance check`` が ``valid=false`` になり、台帳 CSV の手編集で辻褄を合わせる
ことになる。台帳を手で編集する運用が常態化すると台帳自体の信頼性が落ちるので、
書かれる前に 1 行知らせる。

**失敗させない。** 取得できない runtime は実在するので ``unknown`` 自体は許容する
（provenance の必須化はスコープ外）。
"""

from __future__ import annotations

import sys

from .provenance import ENV_FIELDS


def warn_if_unresolved(metadata, *, config, command: str, stream=None) -> bool:
    """AI 情報が既定値のままなら stderr へ 1 行出す。出したら True。

    provenance が無効なプロジェクトでは何も出さない（出力を一切変えない）。
    """
    enabled = bool(getattr(config, "provenance_enabled", False)) or (
        getattr(config, "provenance_manager", None) == "repo"
    )
    if not enabled or not metadata.is_unresolved():
        return False

    flags = {
        "new": "--ai-agent / --ai-runtime / --ai-provider / --ai-model-id / "
               "--ai-model-display / --ai-model-source",
        "provenance start": "--agent / --runtime / --provider / --model-id / "
                            "--model-display / --model-source",
    }.get(command, "--agent 等")
    envs = " / ".join(ENV_FIELDS[name] for name in ("agent", "runtime", "provider"))
    out = stream or sys.stderr
    print(
        f"warning: 作成 AI が unknown のまま記録されます（{command}）。"
        f"{flags} を渡すか、環境変数（{envs} 等）を設定すると実値で残ります",
        file=out,
    )
    return True
