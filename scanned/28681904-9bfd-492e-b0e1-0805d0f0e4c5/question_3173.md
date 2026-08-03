# Q3173: runInContainerWithExec concurrent exec or log requests cross-bind responses

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `runInContainerWithExec` mix the response or exit state from one exec or log request with another concurrent request?

## Target
- File/function: executors/kubernetes/kubernetes.go: runInContainerWithExec
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, concurrent exec or log requests
- Exploit idea: cross-bind mutable request state across simultaneous operations
- Invariant to test: each exec or log request must keep isolated mutable state
- Expected Immunefi impact: wrong-result reporting or output tampering
- Fast validation: run concurrent exec and log requests and verify state isolation
