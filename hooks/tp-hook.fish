# tp deskflight hook (fish) -- add to ~/.config/fish/config.fish:  tp hook fish | source
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

