#!/bin/sh
# Req 10.3: every Check runs as the Grader_User, not as the learner.
# Docker: the image users, grader uid 1001 named "grader" (learner 1000).
# Kubernetes, pod layout B (ADR 0009): the grader container runs as uid 10001, which has no name
# in the image; the learner (uid 10000) runs in a separate container. Any other uid fails.
case "$(/usr/bin/id -u)" in
    1001) [ "$(/usr/bin/id -un)" = "grader" ] || exit 1 ;;
    10001) ;;
    *) exit 1 ;;
esac
exit 0
