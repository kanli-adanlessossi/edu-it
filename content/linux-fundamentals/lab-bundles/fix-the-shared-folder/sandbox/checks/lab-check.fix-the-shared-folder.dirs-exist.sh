#!/bin/sh
# Check dirs-exist. Result channel: the exit code only.
# Tools: absolute paths only (see lab-images/README.md). stat is /bin/stat: Alpine installs it there,
# not in /usr/bin.
#   0  pass
#   11 missing-docs        12 missing-scripts        13 not-a-directory
#   90 unsafe-path (symlink, FIFO, device node or socket where a directory is expected)
# Never follows symlinks: /bin/stat without -L, type tested before anything else is done.

TEAM=/workspace/team

# Prints the file type of $1 without following a final symlink; prints nothing when missing.
ftype() {
  /bin/stat -c '%F' -- "$1" 2>/dev/null
}

# Classify $1: returns 0 directory, 1 missing, 2 regular file, 90 anything unsafe.
classify() {
  case "$(ftype "$1")" in
    "") return 1 ;;
    directory) return 0 ;;
    "regular file" | "regular empty file") return 2 ;;
    *) return 90 ;;
  esac
}

classify "$TEAM"
case $? in
  0) ;;
  1) exit 11 ;;
  2) exit 13 ;;
  *) exit 90 ;;
esac

classify "$TEAM/docs"
docs=$?
classify "$TEAM/scripts"
scripts=$?

# An unsafe entry wins over every other result.
[ "$docs" -eq 90 ] && exit 90
[ "$scripts" -eq 90 ] && exit 90
[ "$docs" -eq 1 ] && exit 11
[ "$docs" -eq 2 ] && exit 13
[ "$scripts" -eq 1 ] && exit 12
[ "$scripts" -eq 2 ] && exit 13
exit 0
