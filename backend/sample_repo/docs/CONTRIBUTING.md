# Contributing

- Every change to engine/ must come with a test in tests/.
- Keep functions small and pure; validation functions return lists of violation strings, never raise.
- Branch names follow task-<task id> or fix/<short-name>.
- Run `pytest` before opening a pull request.
