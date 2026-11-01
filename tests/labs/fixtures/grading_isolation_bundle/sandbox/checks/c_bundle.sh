#!/bin/sh
# Req 10.1: the copied bundle is root-owned and not writable by the learner (nor by group/others).
# Only the Grader_User's own working directory /grader/run is excluded from the scan.
for path in /grader /grader/checks /grader/checks/*.sh; do
    [ "$(/bin/stat -c %u "$path")" = "0" ] || exit 1
done
bad="$(/usr/bin/find /grader -path /grader/run -prune -o \
    \( -user 1000 -o -group 1000 -o -perm -0020 -o -perm -0002 \) -print)"
[ -z "$bad" ] || exit 1
[ ! -w /grader/checks ] || exit 1
exit 0
