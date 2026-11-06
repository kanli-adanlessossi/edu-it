#!/bin/sh
# Req 10.3: every Check runs as the Grader_User, not as the learner.
# Docker and Kubernetes (pod layout B, ADR 0009) both run Checks as the Lab_Image user grader,
# uid 1001 named "grader"; on Kubernetes the learner (uid 1000) runs in a separate container.
# Any other uid or name fails.
[ "$(/usr/bin/id -u)" = "1001" ] || exit 1
[ "$(/usr/bin/id -un)" = "grader" ] || exit 1
exit 0
