# Reference copy. Use the live version instead:  tp hook zsh
# (it fills in the real path of your tp executable; this copy assumes
#  /home/zephyrus/.local/bin/tp, as written by install.sh)
# tp deskflight hook (zsh) v0.3 -- in ~/.zshrc:  eval "$(tp hook zsh)"

# wrap this interactive shell in `tp pty` once, for output capture
if [[ -o interactive && -z "${TP_NO_PTY:-}" && "${__TP_WRAPPED:-}" != "$$" ]]; then
  if [[ "${TP_PTY:-}" != "$(tty 2>/dev/null)" && -x '/home/zephyrus/.local/bin/tp' ]]; then
    export __TP_WRAPPED=$$
    exec '/home/zephyrus/.local/bin/tp' pty "$(command -v zsh)"
  fi
fi
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
