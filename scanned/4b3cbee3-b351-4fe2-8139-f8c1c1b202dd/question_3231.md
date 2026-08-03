# Q3231: captureContainerLogs lower-trust pod state survives into protected jobs

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `captureContainerLogs` reuse pod-side state from an unprotected job in a later protected or unrelated job?

## Target
- File/function: executors/kubernetes/kubernetes.go: captureContainerLogs
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, protected and unprotected jobs plus shared pod-side state
- Exploit idea: carry pod-side state across a trust boundary
- Invariant to test: pod-side exec and log state must be reset across trust boundaries
- Expected Immunefi impact: protected-job escalation or stale-state reuse
- Fast validation: seed lower-trust pod state and verify protected jobs do not inherit it
