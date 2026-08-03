# Q3203: captureServiceContainersLogs helper or service logs leak protected data

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `captureServiceContainersLogs` surface helper or service output in an attacker-visible path that reveals secrets or protected data?

## Target
- File/function: executors/kubernetes/kubernetes.go: captureServiceContainersLogs
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, helper and service output, log capture timing
- Exploit idea: route higher-trust output through attacker-visible log capture
- Invariant to test: helper and service logs must not leak protected data into attacker-visible streams
- Expected Immunefi impact: secret exposure across container roles
- Fast validation: emit sensitive output from helper or service containers and verify it remains hidden
