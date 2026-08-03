# Q3360: TerminalConnect session cleanup leaves stale routable state

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `TerminalConnect` clean up the wrong proxy session while leaving the attacker-selected session state alive for reuse?

## Target
- File/function: executors/kubernetes/terminal.go: TerminalConnect
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, cleanup timing and colliding session identities
- Exploit idea: redirect cleanup away from the stale session state the attacker wants to keep
- Invariant to test: session cleanup must remove all state for the ended live session
- Expected Immunefi impact: persistent stale-session hijack
- Fast validation: race cleanup with colliding sessions and verify ended session state is fully removed
