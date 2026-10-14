# Reference copy. Use the live version instead:  tp hook bash
# (it fills in the real path of your tp executable; this copy assumes
#  /home/zephyrus/.local/bin/tp, as written by install.sh)
# tp deskflight hook (bash) v0.3 -- in ~/.bashrc:  eval "$(tp hook bash)"
# Tip: set  HISTCONTROL=ignorespace  so a space-prefixed command is skipped.

# wrap this interactive shell in `tp pty` once, for output capture
if [[ $- == *i* && -z "${TP_NO_PTY:-}" && "${__TP_WRAPPED:-}" != "$$" ]]; then
  if [[ "${TP_PTY:-}" != "$(tty 2>/dev/null)" && -x '/home/zephyrus/.local/bin/tp' ]]; then
    export __TP_WRAPPED=$$
    exec '/home/zephyrus/.local/bin/tp' pty "$BASH"
  fi
fi
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
