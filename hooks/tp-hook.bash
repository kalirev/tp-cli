# tp deskflight hook (bash) -- add to ~/.bashrc:  eval "$(tp hook bash)"
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

