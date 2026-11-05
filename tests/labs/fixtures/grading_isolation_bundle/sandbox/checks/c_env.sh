#!/bin/sh
# Req 10.4: clean environment. Only variables defined by Config (PATH, LANG, HOME) plus the ones
# the shell itself maintains; nothing inherited from the learner's shell or the image.
# HOSTNAME is injected into every exec by the Docker daemon (not learner-controlled).
# EDU_IT_EXEC is the Kubernetes provider's random per-command marker (ADR 0009; provider-injected,
# not learner-controlled); that provider clears everything else with ``env -i``.
for name in $(/usr/bin/env | /usr/bin/cut -d= -f1); do
    case "$name" in
        PATH | LANG | HOME | PWD | OLDPWD | SHLVL | _ | HOSTNAME | EDU_IT_EXEC) ;;
        *) exit 1 ;;
    esac
done
[ "$PATH" = "/usr/bin:/bin" ] || exit 1
[ "$LANG" = "C" ] || exit 1
[ "$HOME" = "/grader/run" ] || exit 1
exit 0
