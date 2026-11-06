#!/bin/sh
# Req 10.7: while a Check runs, every learner process (PID 1, the init, excepted) is stopped.
# The test harness samples the learner's counter before and after this Check, which sleeps so
# that a still-running learner process would be seen advancing it.
# Docker: one process namespace; the Check sees the learner's processes, all must be stopped.
# Kubernetes, pod layout B (ADR 0009): the grader container has its own process namespace, so the
# Check must see NO learner process at all (GradeRun verifies the freeze inside the learner
# container); the counter sampling around this Check still proves the freeze.
# Both backends run the Check as grader 1001; the owner of /grader tells them apart (root on
# Docker, the owner container uid 1002 on Kubernetes, see c_bundle.sh).
[ "$(/usr/bin/id -u)" = "1001" ] || exit 1
case "$(/bin/stat -c %u /grader)" in
    0) separate=0 ;;
    1002) separate=1 ;;
    *) exit 1 ;;
esac
/bin/ps -e -o user= -o stat= -o pid= | {
    seen=0
    while read -r user stat pid; do
        [ "$pid" = "1" ] && continue
        if [ "$user" = "learner" ] || [ "$user" = "1000" ]; then
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
