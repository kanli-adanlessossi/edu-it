#!/bin/sh
# Req 10.9: the outcome comes from the Check's own exit status. Output that says FAIL (or that a
# learner could imitate) is data only; this Check exits 0 and must be recorded as passed.
echo "FAIL"
echo "FAIL" >&2
exit 0
