#!/bin/sh
# Check no-world-writable. Result channel: the exit code only.
# Tools: absolute paths only (see lab-images/README.md). stat is /bin/stat: Alpine installs it there,
# not in /usr/bin.
#   0  pass (no entry under /workspace/team, the folder included, is world-writable)
#   31 world-writable-remains
#   90 unsafe-path (symlink, FIFO, device node or socket in place of /workspace/team)
# find never follows symlinks (default -P) and does not open any file. Symlinks themselves are
# ignored: their own mode bits are not enforced.

TEAM=/workspace/team

case "$(/bin/stat -c '%F' -- "$TEAM" 2>/dev/null)" in
  directory | "regular file" | "regular empty file") ;;
  "") exit 0 ;; # nothing left to be writable; the other Checks report the missing folder
  *) exit 90 ;;
esac

found=$(/usr/bin/find -P "$TEAM" -perm -0002 ! -type l -print -quit 2>/dev/null) || exit 2
[ -n "$found" ] && exit 31
exit 0
