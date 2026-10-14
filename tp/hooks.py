"""Shell hooks that feed the journal from every terminal.

Each hook does three small things:

1. (optional) Wraps the interactive shell in `tp pty` once, at startup, so
   output can be captured. Guarded three ways against loops and lock-outs:
   only when interactive, only when the shell isn't already inside its own
   tp pty (TP_PTY == this tty), and never twice for the same PID. TP_NO_PTY=1
   in the environment disables it. If `tp pty` can't set up a PTY it execs the
   shell directly, so you always get a terminal.
2. Prints a start marker before each command and an end marker after it
   (only inside a tp pty). The wrapper strips them and slices the output.
3. Calls `tp _record` after each command with the command, cwd, exit code,
   timing and the marker id.

Guards kept from v0.2: every hook no-ops when `tp` is missing; a command typed
with a leading space is skipped (with its output); `tp ...` control commands
are filtered inside `tp _record`.
"""
from __future__ import annotations

import shutil

_FISH_PTY = r'''
    # wrap this interactive shell in `tp pty` once, for output capture
    if not set -q TP_NO_PTY; and test "$__TP_WRAPPED" != "$fish_pid"
        if test "$TP_PTY" != (tty 2>/dev/null); and test -x '@EXE@'
            set -gx __TP_WRAPPED $fish_pid
            exec '@EXE@' pty (status fish-path)
        end
    end
'''

FISH = r'''# tp deskflight hook (fish) v0.3 -- in ~/.config/fish/config.fish:  tp hook fish | source
set -g __tp_marks 0
# the shell outside the wrapper already greeted you; don't greet twice
if set -q TP_PTY; and set -q __TP_WRAPPED
    function fish_greeting; end
end
# Runs once, at the first prompt: here stdin is the terminal (inside
# `... | source` it is a pipe, so `tty` and `exec` can't be used yet).
function __tp_init --on-event fish_prompt
    functions -e __tp_init
    status is-interactive; or return
@PTY@
    if set -q TP_PTY; and test "$TP_PTY" = (tty 2>/dev/null)
        set -g __tp_marks 1
    end
end
function __tp_preexec --on-event fish_preexec
    set -g __tp_cmd $argv[1]
    set -g __tp_start (date +%s)
    set -g __tp_id ''
    if test $__tp_marks = 1; and not string match -q ' *' -- "$argv[1]"
        set -g __tp_id (random)(random)$fish_pid
        printf '\e]7777;tp;S;%s\a' $__tp_id
    end
end
function __tp_postexec --on-event fish_postexec
    set -l ec $status
    set -l id $__tp_id
    set -g __tp_id ''
    test -n "$id"; and printf '\e]7777;tp;E;%s\a' $id
    type -q tp; or return
    set -l cmd $__tp_cmd
    set -e __tp_cmd
    test -z "$cmd"; and return
    string match -q ' *' -- "$cmd"; and return
    set -l dur 0
    set -l st (date +%s)
    if set -q __tp_start
        set dur (math $st - $__tp_start)
        set st $__tp_start
    end
    set -l term "f"$fish_pid
    set -q TP_TERM; and set term $TP_TERM
    tp _record --exit $ec --dur $dur --start $st --cwd "$PWD" --term "$term" --pid $fish_pid --id "$id" --cmd "$cmd"
end
'''

_BASH_PTY = r'''
# wrap this interactive shell in `tp pty` once, for output capture
if [[ $- == *i* && -z "${TP_NO_PTY:-}" && "${__TP_WRAPPED:-}" != "$$" ]]; then
  if [[ "${TP_PTY:-}" != "$(tty 2>/dev/null)" && -x '@EXE@' ]]; then
    export __TP_WRAPPED=$$
    exec '@EXE@' pty "$BASH"
  fi
fi
'''

BASH = r'''# tp deskflight hook (bash) v0.3 -- in ~/.bashrc:  eval "$(tp hook bash)"
# Tip: set  HISTCONTROL=ignorespace  so a space-prefixed command is skipped.
@PTY@
__tp_last=""
__tp_first=1   # the first prompt's `history 1` is from an old session: skip it
__tp_id=""
__tp_marks=0
[[ -n "${TP_PTY:-}" && "$TP_PTY" == "$(tty 2>/dev/null)" ]] && __tp_marks=1
if [[ $__tp_marks == 1 ]]; then
  __tp_id="$RANDOM$RANDOM$$"
  PS0="${PS0:-}"'\e]7777;tp;S;${__tp_id}\a'   # bash >= 4.4: printed before each command
fi
__tp_precmd() {
  local ec=$?
  local id="$__tp_id"
  [[ $__tp_marks == 1 ]] && __tp_id="$RANDOM$RANDOM$$"   # id for the next command
  local h num cmd
  h=$(history 1 2>/dev/null)
  read -r num cmd <<< "$h"
  if [[ -n "$__tp_first" ]]; then
    __tp_first=""; __tp_last="$num"
  fi
  if [[ -z "$cmd" || "$num" == "$__tp_last" ]]; then   # bare Enter / ignored command
    [[ $__tp_marks == 1 ]] && printf '\e]7777;tp;X;%s\a' "$id"
    return
  fi
  __tp_last="$num"
  [[ $__tp_marks == 1 ]] && printf '\e]7777;tp;E;%s\a' "$id"
  command -v tp >/dev/null 2>&1 || return
  tp _record --exit "$ec" --cwd "$PWD" --term "${TP_TERM:-b$$}" --pid $$ --id "$id" --cmd "$cmd"
}
case "${PROMPT_COMMAND:-}" in
  *__tp_precmd*) ;;
  *) PROMPT_COMMAND="__tp_precmd${PROMPT_COMMAND:+; $PROMPT_COMMAND}";;
esac
'''

_ZSH_PTY = r'''
# wrap this interactive shell in `tp pty` once, for output capture
if [[ -o interactive && -z "${TP_NO_PTY:-}" && "${__TP_WRAPPED:-}" != "$$" ]]; then
  if [[ "${TP_PTY:-}" != "$(tty 2>/dev/null)" && -x '@EXE@' ]]; then
    export __TP_WRAPPED=$$
    exec '@EXE@' pty "$(command -v zsh)"
  fi
fi
'''

ZSH = r'''# tp deskflight hook (zsh) v0.3 -- in ~/.zshrc:  eval "$(tp hook zsh)"
@PTY@
zmodload zsh/datetime 2>/dev/null
__tp_marks=0
[[ -n "${TP_PTY:-}" && "$TP_PTY" == "$(tty 2>/dev/null)" ]] && __tp_marks=1
__tp_preexec() {
  __tp_cmd="$1"; __tp_start=${EPOCHSECONDS:-0}; __tp_id=""
  if [[ $__tp_marks == 1 && "$1" != " "* ]]; then
    __tp_id="$RANDOM$RANDOM$$"
    printf '\e]7777;tp;S;%s\a' "$__tp_id"
  fi
}
__tp_precmd() {
  local ec=$?
  local id="${__tp_id:-}"; __tp_id=""
  [[ -n "$id" ]] && printf '\e]7777;tp;E;%s\a' "$id"
  command -v tp >/dev/null 2>&1 || return
  local cmd="${__tp_cmd:-}"; __tp_cmd=""
  [ -z "$cmd" ] && return
  [[ "$cmd" == " "* ]] && return
  local dur=0
  [ -n "${__tp_start:-}" ] && dur=$(( ${EPOCHSECONDS:-0} - __tp_start ))
  tp _record --exit "$ec" --dur "$dur" --start "${__tp_start:-0}" --cwd "$PWD" --term "${TP_TERM:-z$$}" --pid $$ --id "$id" --cmd "$cmd"
}
autoload -Uz add-zsh-hook
add-zsh-hook preexec __tp_preexec
add-zsh-hook precmd __tp_precmd
'''

_PTY_BLOCKS = {"fish": _FISH_PTY, "bash": _BASH_PTY, "zsh": _ZSH_PTY}
_HOOKS = {"fish": FISH, "bash": BASH, "zsh": ZSH}


def tp_executable() -> str | None:
    """Absolute path of a real `tp` program (not a shell function/alias)."""
    return shutil.which("tp")


def for_shell(shell: str, pty: bool = True, exe: str | None = None) -> str | None:
    src = _HOOKS.get(shell)
    if src is None:
        return None
    exe = exe if exe is not None else tp_executable()
    if pty and exe and "'" not in exe:
        block = _PTY_BLOCKS[shell].replace("@EXE@", exe)
    elif pty:
        block = ("\n# output capture is off: no `tp` executable on PATH "
                 "(run install.sh, then open a new terminal)\n")
    else:
        block = "\n# output capture disabled (tp config pty off)\n"
    return src.replace("@PTY@", block.rstrip("\n"))
