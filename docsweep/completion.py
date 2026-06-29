"""``docsweep completion bash|zsh|pwsh`` — シェル補完スクリプト生成。

``--tag`` は ``.docsweep.yaml`` の ``known_tags``、``--status`` は ``StateModel`` の語彙
（key + ja/en ラベル）から動的に組み立てる。生成は stdout のみで、ユーザーが
``eval`` / dot source / Add-Content で取り込む運用。
"""

from __future__ import annotations

from .config import Config

SUPPORTED_SHELLS: tuple[str, ...] = ("bash", "zsh", "pwsh")

# 全サブコマンド集合（cli.py の build_parser と揃える）。
_SUBCOMMANDS: tuple[str, ...] = (
    "scan", "triage", "apply", "sweep", "serve", "promote", "index", "pending",
    "report", "summary", "new", "review", "inject", "eject", "list", "mcp",
    "migrate-frontmatter", "fix-related", "show", "stale", "context", "claim",
    "config", "timeline", "find", "completion",
)


def _status_vocab(config: Config) -> list[str]:
    """state key と各言語ラベルをマージした補完候補。"""
    vocab: list[str] = []
    seen: set[str] = set()
    for s in config.state_model.states:
        for v in (s.key, *s.labels.values()):
            if v and v not in seen:
                seen.add(v)
                vocab.append(v)
    return vocab


def _review_status_vocab(config: Config) -> list[str]:
    """``review_status`` の補完候補（stale_thresholds のキー）。"""
    return list(config.stale_thresholds.keys())


def _type_vocab() -> list[str]:
    return ["plan", "bugfix", "pending"]


def _bash(config: Config) -> str:
    cmds = " ".join(_SUBCOMMANDS)
    tags = " ".join(config.known_tags)
    statuses = " ".join(_status_vocab(config))
    rstatuses = " ".join(_review_status_vocab(config))
    types = " ".join(_type_vocab())
    return f"""# docsweep bash completion (generated)
_docsweep() {{
    local cur prev cmds tags statuses rstatuses types
    COMPREPLY=()
    cur="${{COMP_WORDS[COMP_CWORD]}}"
    prev="${{COMP_WORDS[COMP_CWORD-1]}}"
    cmds="{cmds}"
    tags="{tags}"
    statuses="{statuses}"
    rstatuses="{rstatuses}"
    types="{types}"
    case "$prev" in
        --tag) COMPREPLY=( $(compgen -W "$tags" -- "$cur") ); return 0 ;;
        --status) COMPREPLY=( $(compgen -W "$statuses" -- "$cur") ); return 0 ;;
        --review-status) COMPREPLY=( $(compgen -W "$rstatuses" -- "$cur") ); return 0 ;;
        --type) COMPREPLY=( $(compgen -W "$types" -- "$cur") ); return 0 ;;
    esac
    if [ "$COMP_CWORD" -eq 1 ]; then
        COMPREPLY=( $(compgen -W "$cmds" -- "$cur") )
        return 0
    fi
}}
complete -F _docsweep docsweep
"""


def _zsh(config: Config) -> str:
    cmds = " ".join(_SUBCOMMANDS)
    tags = " ".join(config.known_tags)
    statuses = " ".join(_status_vocab(config))
    rstatuses = " ".join(_review_status_vocab(config))
    types = " ".join(_type_vocab())
    return f"""#compdef docsweep
# docsweep zsh completion (generated)
_docsweep() {{
    local -a cmds tags statuses rstatuses types
    cmds=({cmds})
    tags=({tags})
    statuses=({statuses})
    rstatuses=({rstatuses})
    types=({types})
    local context state line
    _arguments -C \\
        '--tag[tag]:tag:->tag' \\
        '--status[status]:status:->status' \\
        '--review-status[review status]:rs:->rs' \\
        '--type[type]:type:->type' \\
        '1: :->cmd' \\
        '*::arg:->args'
    case $state in
        cmd) _describe 'docsweep command' cmds ;;
        tag) compadd -- $tags ;;
        status) compadd -- $statuses ;;
        rs) compadd -- $rstatuses ;;
        type) compadd -- $types ;;
    esac
}}
compdef _docsweep docsweep
"""


def _pwsh(config: Config) -> str:
    def _q(items: list[str]) -> str:
        return ", ".join(f"'{s}'" for s in items)

    cmds = _q(list(_SUBCOMMANDS))
    tags = _q(config.known_tags)
    statuses = _q(_status_vocab(config))
    rstatuses = _q(_review_status_vocab(config))
    types = _q(_type_vocab())
    return f"""# docsweep PowerShell completion (generated)
Register-ArgumentCompleter -Native -CommandName docsweep -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)
    $tokens = $commandAst.CommandElements | ForEach-Object {{ $_.ToString() }}
    $last = if ($tokens.Count -ge 2) {{ $tokens[$tokens.Count - 2] }} else {{ $null }}
    $cmds = @({cmds})
    $tags = @({tags})
    $statuses = @({statuses})
    $rstatuses = @({rstatuses})
    $types = @({types})
    $candidates = switch ($last) {{
        '--tag' {{ $tags }}
        '--status' {{ $statuses }}
        '--review-status' {{ $rstatuses }}
        '--type' {{ $types }}
        default {{
            if ($tokens.Count -le 2) {{ $cmds }} else {{ @() }}
        }}
    }}
    $candidates | Where-Object {{ $_ -like "$wordToComplete*" }} | ForEach-Object {{
        [System.Management.Automation.CompletionResult]::new($_, $_, 'ParameterValue', $_)
    }}
}}
"""


def render_completion(shell: str, config: Config) -> str:
    """指定シェル用補完スクリプトを返す。"""
    s = shell.strip().lower()
    if s not in SUPPORTED_SHELLS:
        raise ValueError(
            f"未対応のシェル: {shell!r}（対応: {', '.join(SUPPORTED_SHELLS)}）"
        )
    if s == "bash":
        return _bash(config)
    if s == "zsh":
        return _zsh(config)
    return _pwsh(config)
