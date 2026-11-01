#!/bin/sh
# Req 10.4, 10.5: explicit working directory, fixed PATH made only of root-owned directories
# that neither group nor others can write to.
[ "$(/bin/pwd)" = "/grader/run" ] || exit 1
[ "$PATH" = "/usr/bin:/bin" ] || exit 1
for dir in /usr/bin /bin; do
    [ "$(/bin/stat -L -c %u "$dir")" = "0" ] || exit 1
    case "$(/bin/stat -L -c %A "$dir")" in
        ?????-??-*) ;;
        *) exit 1 ;;
    esac
done
exit 0
