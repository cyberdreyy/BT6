# Q3144: runInContainer stale pod or container identity is trusted

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `runInContainer` treat stale pod or container identity as the live exec or log target after the workload changed?

## Target
- File/function: executors/kubernetes/kubernetes.go: runInContainer
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, pod restarts and container replacement
- Exploit idea: reuse stale identity state across live workload changes
- Invariant to test: exec and log state must stay bound to the current live workload instance
- Expected Immunefi impact: wrong-container access or stale-state trust
- Fast validation: replace workloads during attach and verify stale identities are rejected
