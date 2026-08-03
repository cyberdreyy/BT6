# Q3236: captureContainerLogs service log capture persists after service changes

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `captureContainerLogs` keep capturing one service stream after the live service has changed identity?

## Target
- File/function: executors/kubernetes/kubernetes.go: captureContainerLogs
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, service churn and log capture state
- Exploit idea: reuse stale service-log state across service replacement
- Invariant to test: service log capture must follow the current live service only
- Expected Immunefi impact: wrong-service disclosure or stale-state trust
- Fast validation: replace services while capturing logs and verify the stream rebinds or stops safely
