# tp deskflight hook (zsh) -- add to ~/.zshrc:  eval "$(tp hook zsh)"
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

