# Q3142: runInContainer build and service logs mix together

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `runInContainer` mix build-container bytes and service-container bytes into one attacker-visible log stream?

## Target
- File/function: executors/kubernetes/kubernetes.go: runInContainer
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, concurrent build and service output
- Exploit idea: lose byte ownership across container log streams
- Invariant to test: log output must preserve exact container ownership
- Expected Immunefi impact: secret exposure or output tampering
- Fast validation: emit distinct markers from build and service containers and verify no mixing occurs
