# Q2948: forwardLogLine watch state from a prior container drives the next one

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `forwardLogLine` apply watch or readiness state from an earlier container instance to a new container instance?

## Target
- File/function: executors/kubernetes/kubernetes.go: forwardLogLine
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, container replacement and watch state
- Exploit idea: reuse stale watch state after container turnover
- Invariant to test: watch state must remain bound to the current container instance
- Expected Immunefi impact: stale-state trust or wrong-container access
- Fast validation: replace containers and verify watch state is refreshed
