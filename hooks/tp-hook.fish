# Reference copy. Use the live version instead:  tp hook fish
# (it fills in the real path of your tp executable; this copy assumes
#  /home/zephyrus/.local/bin/tp, as written by install.sh)
# tp deskflight hook (fish) v0.3 -- in ~/.config/fish/config.fish:  tp hook fish | source
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

    # wrap this interactive shell in `tp pty` once, for output capture
    if not set -q TP_NO_PTY; and test "$__TP_WRAPPED" != "$fish_pid"
        if test "$TP_PTY" != (tty 2>/dev/null); and test -x '/home/zephyrus/.local/bin/tp'
            set -gx __TP_WRAPPED $fish_pid
            exec '/home/zephyrus/.local/bin/tp' pty (status fish-path)
        end
    end
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
