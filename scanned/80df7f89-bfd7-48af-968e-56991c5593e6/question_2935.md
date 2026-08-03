# Q2935: processLogs build output steers which container logs are read

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `processLogs` use attacker-controlled build output to influence later log target selection or parsing?

## Target
- File/function: executors/kubernetes/kubernetes.go: processLogs
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, build output and later log-capture logic
- Exploit idea: persist lower-trust output into later log-target decisions
- Invariant to test: log target selection must not depend on attacker-controlled prior output
- Expected Immunefi impact: wrong-container disclosure or log confusion
- Fast validation: emit crafted build output and verify later log capture still targets the intended container
