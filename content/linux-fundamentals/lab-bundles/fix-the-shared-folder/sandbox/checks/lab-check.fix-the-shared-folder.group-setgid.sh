#!/bin/sh
# Check group-setgid. Result channel: the exit code only.
# Tools: absolute paths only (see lab-images/README.md). stat is /bin/stat: Alpine installs it there,
# not in /usr/bin.
#   0  pass
#   21 wrong-group         22 setgid-missing         23 team-not-directory
#   90 unsafe-path (symlink, FIFO, device node or socket in place of /workspace/team)
# Never follows symlinks: /bin/stat without -L, type tested before anything else is done.

TEAM=/workspace/team

case "$(/bin/stat -c '%F' -- "$TEAM" 2>/dev/null)" in
  directory) ;;
  "" | "regular file" | "regular empty file") exit 23 ;;
  *) exit 90 ;;
esac

# Group name and octal mode (four digits when a special bit is set, e.g. 2775).
info=$(/bin/stat -c '%G %a' -- "$TEAM" 2>/dev/null) || exit 23
group=${info%% *}
mode=${info#* }

[ "$group" = "devs" ] || exit 21

case "$mode" in
  2??? | 3??? | 6??? | 7???) exit 0 ;;
  *) exit 22 ;;
esac
