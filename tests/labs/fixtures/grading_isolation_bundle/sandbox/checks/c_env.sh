#!/bin/sh
# Req 10.4: clean environment. Only variables defined by Config (PATH, LANG, HOME) plus the ones
# the shell itself maintains; nothing inherited from the learner's shell or the image.
# HOSTNAME is injected into every exec by the Docker daemon (not learner-controlled).
for name in $(/usr/bin/env | /usr/bin/cut -d= -f1); do
    case "$name" in
        PATH | LANG | HOME | PWD | OLDPWD | SHLVL | _ | HOSTNAME) ;;
        *) exit 1 ;;
    esac
done
[ "$PATH" = "/usr/bin:/bin" ] || exit 1
[ "$LANG" = "C" ] || exit 1
[ "$HOME" = "/grader/run" ] || exit 1
exit 0
