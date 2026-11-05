#!/bin/sh
# Req 10.1: the copied bundle is owned by the bundle owner and not writable by the learner (nor by
# group/others). Only the Grader_User's own working directory /grader/run is excluded from the scan.
# The identities follow from the uid this Check runs as (see c_identity.sh):
#   Docker:     grader 1001  -> owner root (0),          learner 1000
#   Kubernetes: grader 10001 -> owner container (10002), learner 10000 (pod layout B, ADR 0009)
case "$(/usr/bin/id -u)" in
    1001) owner=0 learner=1000 ;;
    10001) owner=10002 learner=10000 ;;
    *) exit 1 ;;
esac
for path in /grader /grader/checks /grader/checks/*.sh; do
    [ "$(/bin/stat -c %u "$path")" = "$owner" ] || exit 1
done
bad="$(/usr/bin/find /grader -path /grader/run -prune -o \
    \( -user "$learner" -o -group "$learner" -o -perm -0020 -o -perm -0002 \) -print)"
[ -z "$bad" ] || exit 1
[ ! -w /grader/checks ] || exit 1
exit 0
