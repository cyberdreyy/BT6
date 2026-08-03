# Q3214: captureServiceContainersLogs termination or retry state from one container affects another

## Question
Can an unprivileged GitLab user or pipeline author enter through Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services and make `captureServiceContainersLogs` apply termination or retry state observed for one container to another live container?

## Target
- File/function: executors/kubernetes/kubernetes.go: captureServiceContainersLogs
- Entrypoint: Kubernetes exec, attach, or log flows reachable from an unprivileged job and its services
- Attacker controls: job script, service definitions, log output, container timing, and reconnect behavior, termination signals, retries, and container churn
- Exploit idea: reuse mutable stop or retry state across targets
- Invariant to test: termination and retry state must remain scoped to one container
- Expected Immunefi impact: wrong-container interruption or stale-state trust
- Fast validation: cause one container to stop while another continues and verify state does not cross
