# grading_isolation_bundle

Minimal terminal-lab Check_Bundle used by `tests/labs/test_docker_grading_isolation.py`
(hands-on-labs task 8.3, Requirements 10.1 to 10.10). Layout matches production bundles:
`sandbox/` is copied to `/grader` at grading time. Each Check reports only through its exit
status. No Check reads a learner-writable path.
