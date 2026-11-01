#!/bin/sh
# Req 10.3: every Check runs as the Grader_User (uid 1001), not as the learner (uid 1000).
[ "$(/usr/bin/id -u)" = "1001" ] || exit 1
[ "$(/usr/bin/id -un)" = "grader" ] || exit 1
exit 0
