# Q2938: processLogs per-container secrets appear in the wrong exec or log path

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `processLogs` expose per-container secrets through another container’s exec or log path?

## Target
- File/function: executors/kubernetes/kubernetes.go: processLogs
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, per-container secrets and multiple live targets
- Exploit idea: cross one secret boundary between containers inside the same pod
- Invariant to test: per-container secret scope must remain exact for exec and log paths
- Expected Immunefi impact: secret exposure across container roles
- Fast validation: trace secret visibility across containers and verify exact scope
