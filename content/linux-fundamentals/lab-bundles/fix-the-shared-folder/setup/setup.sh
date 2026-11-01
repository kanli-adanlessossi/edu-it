#!/bin/bash
# Setup for the lab "Fix the shared folder". Runs as the learner user (no root, no capabilities).
# Idempotent: running it again leaves the same state and never undoes the learner's work.
set -eu

TEAM=/workspace/team
NOTES="$TEAM/shared-notes.txt"

# The learner must be able to use the group "devs" (baked into the image, learner is a member).
# `id -nG` lists the groups of the current user; fail loudly when "devs" is missing.
case " $(/usr/bin/id -nG) " in
  *" devs "*) ;;
  *)
    echo "setup: the learner user is not a member of the group devs" >&2
    exit 1
    ;;
esac

/bin/mkdir -p -- "$TEAM"

# Plant the world-writable file only when it is not there yet.
if [ ! -e "$NOTES" ] && [ ! -L "$NOTES" ]; then
  /usr/bin/printf '%s\n' \
    'Team notes' \
    '- Everyone on the team reads this file.' \
    '- Do not delete it and do not change its content.' >"$NOTES"
  /bin/chmod 0666 -- "$NOTES"
fi
