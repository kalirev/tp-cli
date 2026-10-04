"""Shell hooks that feed the active session from every terminal.

Each hook is tiny: on every command it calls `tp _record`, which decides
whether anything is recording and appends one event. No inline parsing, no
per-shell Python gymnastics - the same robust path for bash, zsh and fish.

Guards:
- every hook no-ops when `tp` isn't on PATH, so a missing/renamed tp can never
  spam your prompt with "command not found";
- a command typed with a leading space is skipped (pair with
  `HISTCONTROL=ignorespace` in bash) so secrets stay out;
- the recorder's own `tp ...` commands are filtered inside `tp _record`.
"""
from __future__ import annotations

BASH = r'''# tp deskflight hook (bash) -- add to ~/.bashrc:  eval "$(tp hook bash)"
# Tip: set  HISTCONTROL=ignorespace  so a space-prefixed command is skipped.
__tp_last=""
__tp_precmd() {
  local ec=$?
  command -v tp >/dev/null 2>&1 || return
  local h num cmd
  h=$(history 1 2>/dev/null) || return
  read -r num cmd <<< "$h"
  [ -z "$cmd" ] && return
  [ "$num" = "$__tp_last" ] && return   # bare Enter -> nothing new
  __tp_last="$num"
  tp _record --exit "$ec" --cwd "$PWD" --term "b$$" --cmd "$cmd"
}
case "${PROMPT_COMMAND:-}" in
  *__tp_precmd*) ;;
  *) PROMPT_COMMAND="__tp_precmd${PROMPT_COMMAND:+; $PROMPT_COMMAND}";;
esac
'''

ZSH = r'''# tp deskflight hook (zsh) -- add to ~/.zshrc:  eval "$(tp hook zsh)"
zmodload zsh/datetime 2>/dev/null
__tp_preexec() { __tp_cmd="$1"; __tp_start=${EPOCHSECONDS:-0} }
__tp_precmd() {
  local ec=$?
  command -v tp >/dev/null 2>&1 || return
  local cmd="${__tp_cmd:-}"; __tp_cmd=""
  [ -z "$cmd" ] && return
  [[ "$cmd" == " "* ]] && return
  local dur=0
  [ -n "${__tp_start:-}" ] && dur=$(( ${EPOCHSECONDS:-0} - __tp_start ))
  tp _record --exit "$ec" --dur "$dur" --cwd "$PWD" --term "z$$" --cmd "$cmd"
}
autoload -Uz add-zsh-hook
add-zsh-hook preexec __tp_preexec
add-zsh-hook precmd __tp_precmd
'''

FISH = r'''# tp deskflight hook (fish) -- add to ~/.config/fish/config.fish:  tp hook fish | source
function __tp_preexec --on-event fish_preexec
  set -g __tp_cmd $argv[1]
  set -g __tp_start (date +%s)
end
function __tp_postexec --on-event fish_postexec
  set -l ec $status
  type -q tp; or return
  set -l cmd $__tp_cmd
  set -e __tp_cmd
  test -z "$cmd"; and return
  string match -q ' *' -- "$cmd"; and return
  set -l dur 0
  if set -q __tp_start
    set dur (math (date +%s) - $__tp_start)
  end
  tp _record --exit $ec --dur $dur --cwd "$PWD" --term "f"$fish_pid --cmd "$cmd"
end
'''


def for_shell(shell: str) -> str | None:
    return {"bash": BASH, "zsh": ZSH, "fish": FISH}.get(shell)
