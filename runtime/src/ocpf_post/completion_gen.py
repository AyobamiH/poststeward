"""Generate shell completions directly from argparse command metadata."""
from __future__ import annotations

from typing import Any


def _prefixes(commands) -> dict[tuple[str, ...], list[str]]:
    result: dict[tuple[str, ...], set[str]] = {}
    for command in commands:
        parts = tuple(command.path.split())
        for index in range(len(parts)):
            result.setdefault(parts[:index], set()).add(parts[index])
    return {key: sorted(value) for key, value in result.items()}


def _words(values) -> str:
    return " ".join(str(value) for value in values)


def _flags(arguments: list[dict[str, Any]]) -> list[str]:
    return sorted({flag for row in arguments for flag in row.get("flags", [])})


def bash_completion(commands, surface) -> str:
    prefixes = _prefixes(commands)
    dollar = "$"
    lines = [
        "# GENERATED from argparse metadata",
        "_ocpf_post_complete() {",
        "  local cur prev",
        "  COMPREPLY=()",
        f'  cur="{dollar}{{COMP_WORDS[COMP_CWORD]}}"',
        f'  prev="{dollar}{{COMP_WORDS[COMP_CWORD-1]}}"',
        f'  if (( COMP_CWORD == 1 )); then COMPREPLY=( $(compgen -W "{_words(prefixes.get((), []))}" -- "{dollar}cur") ); return; fi',
    ]
    for prefix, children in sorted(prefixes.items(), key=lambda item: (len(item[0]), item[0])):
        if not prefix:
            continue
        cond = " && ".join(
            f'[[ "{dollar}{{COMP_WORDS[{index + 1}]}}" == "{token}" ]]'
            for index, token in enumerate(prefix)
        )
        lines.append(
            f'  if (( COMP_CWORD == {len(prefix) + 1} )) && {cond}; then '
            f'COMPREPLY=( $(compgen -W "{_words(children)}" -- "{dollar}cur") ); return; fi'
        )
    for path, row in sorted(surface.items(), key=lambda item: (-len(item[0].split()), item[0])):
        parts = path.split()
        cond = " && ".join(
            f'[[ "{dollar}{{COMP_WORDS[{index + 1}]}}" == "{token}" ]]'
            for index, token in enumerate(parts)
        )
        arguments = row.get("arguments", [])
        lines.append(f"  if {cond}; then")
        for argument in arguments:
            choices = argument.get("choices") or []
            flags = argument.get("flags") or []
            if choices and flags:
                lines.append(
                    f'    case "{dollar}prev" in {"|".join(flags)}) '
                    f'COMPREPLY=( $(compgen -W "{_words(choices)}" -- "{dollar}cur") ); return ;; esac'
                )
        flags = _flags(arguments)
        if flags:
            lines.append(
                f'    if [[ "{dollar}cur" == -* ]]; then '
                f'COMPREPLY=( $(compgen -W "{_words(flags)}" -- "{dollar}cur") ); return; fi'
            )
        lines += ["    return", "  fi"]
    lines += ["}", "complete -F _ocpf_post_complete ocpf-post"]
    return "\n".join(lines) + "\n"


def _zsh_desc(value) -> str:
    return str(value or "option").replace("\\", "\\\\").replace("[", "(").replace("]", ")").replace("'", "")


def zsh_completion(commands, surface) -> str:
    prefixes = _prefixes(commands)
    lines = [
        "#compdef ocpf-post",
        "# GENERATED from argparse metadata",
        "local context state line",
        f"if (( CURRENT == 2 )); then _values 'command' {_words(prefixes.get((), []))}; return; fi",
    ]
    for prefix, children in sorted(prefixes.items(), key=lambda item: (len(item[0]), item[0])):
        if not prefix:
            continue
        cond = " && ".join(
            f"[[ $words[{index + 2}] == '{token}' ]]"
            for index, token in enumerate(prefix)
        )
        lines.append(
            f"if (( CURRENT == {len(prefix) + 2} )) && {cond}; then "
            f"_values 'subcommand' {_words(children)}; return; fi"
        )
    for path, row in sorted(surface.items(), key=lambda item: (-len(item[0].split()), item[0])):
        parts = path.split()
        cond = " && ".join(
            f"[[ $words[{index + 2}] == '{token}' ]]"
            for index, token in enumerate(parts)
        )
        specs = []
        for argument in row.get("arguments", []):
            choices = argument.get("choices") or []
            description = _zsh_desc(argument.get("help"))
            for flag in argument.get("flags") or []:
                if argument.get("nargs") == 0:
                    specs.append(f"'{flag}[{description}]'")
                elif choices:
                    specs.append(f"'{flag}[{description}]:{argument.get('name')}:({_words(choices)})'")
                else:
                    specs.append(f"'{flag}[{description}]:{argument.get('name')}:'")
        if specs:
            lines += [f"if {cond}; then", "  _arguments " + " ".join(specs), "  return", "fi"]
    return "\n".join(lines) + "\n"


def _fish_condition(parts: list[str]) -> str:
    return "; and ".join(f"__fish_seen_subcommand_from {part}" for part in parts)


def _fish_escape(value) -> str:
    return str(value or "").replace("\\", "\\\\").replace("'", "\\'")


def fish_completion(commands, surface) -> str:
    prefixes = _prefixes(commands)
    lines = ["# GENERATED from argparse metadata", "complete -c ocpf-post -f"]
    for name in prefixes.get((), []):
        lines.append(f"complete -c ocpf-post -n '__fish_use_subcommand' -a '{name}'")
    for prefix, children in sorted(prefixes.items(), key=lambda item: (len(item[0]), item[0])):
        if not prefix:
            continue
        condition = _fish_condition(list(prefix))
        for child in children:
            lines.append(f"complete -c ocpf-post -n '{condition}' -a '{child}'")
    for path, row in sorted(surface.items()):
        condition = _fish_condition(path.split())
        for argument in row.get("arguments", []):
            flags = argument.get("flags") or []
            if not flags:
                continue
            command = ["complete", "-c", "ocpf-post", "-n", f"'{condition}'"]
            for flag in flags:
                if flag.startswith("--"):
                    command += ["-l", flag[2:]]
                elif flag.startswith("-") and len(flag) == 2:
                    command += ["-s", flag[1:]]
            if argument.get("help"):
                command += ["-d", f"'{_fish_escape(argument['help'])}'"]
            choices = argument.get("choices") or []
            if choices:
                command += ["-xa", f"'{_words(choices)}'"]
            elif argument.get("nargs") != 0:
                command += ["-r"]
            lines.append(" ".join(command))
    return "\n".join(lines) + "\n"
