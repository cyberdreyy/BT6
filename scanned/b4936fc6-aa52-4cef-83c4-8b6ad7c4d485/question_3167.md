# Q3167: runInContainerWithExec log cursor or stream state is reused across containers

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `runInContainerWithExec` reuse log cursor or stream state from one container when reading another container?

## Target
- File/function: executors/kubernetes/kubernetes.go: runInContainerWithExec
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, cursor state and multiple containers
- Exploit idea: hold mutable log position too broadly across targets
- Invariant to test: log cursor state must remain scoped to one exact container
- Expected Immunefi impact: cross-container disclosure or log tampering
- Fast validation: read multiple container logs in one job and verify cursors remain isolated
