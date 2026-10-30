#!/bin/sh
# Req 10.9: this Check fails by its own exit status, whatever the learner planted in the
# learner-writable locations (/workspace/result*, /tmp/result*, /home/learner/result*) and
# whatever those files say. It deliberately reads none of them.
echo "PASS"
exit 1
