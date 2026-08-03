# Q3345: TerminalConnect stale readiness drives live routing

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `TerminalConnect` treat one stale readiness result as authority for a later different service instance?

## Target
- File/function: executors/kubernetes/terminal.go: TerminalConnect
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, readiness timing and rapid service changes
- Exploit idea: rely on old readiness state for a new live target
- Invariant to test: readiness state must remain bound to the current service instance
- Expected Immunefi impact: wrong-service access or stale-state trust
- Fast validation: replace a ready service and verify proxy routing does not trust stale readiness
