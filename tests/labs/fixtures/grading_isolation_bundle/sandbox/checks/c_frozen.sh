#!/bin/sh
# Req 10.7: while a Check runs, every learner process (PID 1, the init, excepted) is stopped.
# The test harness samples the learner's counter before and after this Check, which sleeps so
# that a still-running learner process would be seen advancing it.
# Docker: one process namespace; the Check sees the learner's processes, all must be stopped.
# Kubernetes, pod layout B (ADR 0009): the grader container has its own process namespace, so the
# Check must see NO learner process at all (GradeRun verifies the freeze inside the learner
# container); the counter sampling around this Check still proves the freeze.
case "$(/usr/bin/id -u)" in
    1001) separate=0 ;;
    10001) separate=1 ;;
    *) exit 1 ;;
esac
/bin/ps -e -o user= -o stat= -o pid= | {
    seen=0
    while read -r user stat pid; do
        [ "$pid" = "1" ] && continue
        if [ "$user" = "learner" ] || [ "$user" = "10000" ]; then
            seen=$((seen + 1))
            case "$stat" in
                T*) ;;
                *) exit 1 ;;
            esac
        fi
    done
    if [ "$separate" = "1" ]; then [ "$seen" -eq 0 ]; else [ "$seen" -ge 1 ]; fi
} || exit 1
/bin/sleep 1.5
exit 0
