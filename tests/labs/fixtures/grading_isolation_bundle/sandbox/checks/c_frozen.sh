#!/bin/sh
# Req 10.7: while a Check runs, every learner process (PID 1, the init, excepted) is stopped.
# The test harness samples the learner's counter before and after this Check, which sleeps so
# that a still-running learner process would be seen advancing it.
/bin/ps -e -o user= -o stat= -o pid= | {
    seen=0
    while read -r user stat pid; do
        [ "$pid" = "1" ] && continue
        if [ "$user" = "learner" ]; then
            seen=$((seen + 1))
            case "$stat" in
                T*) ;;
                *) exit 1 ;;
            esac
        fi
    done
    [ "$seen" -ge 1 ]
} || exit 1
/bin/sleep 1.5
exit 0
