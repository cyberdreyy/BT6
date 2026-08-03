# Q3297: serviceEndpointRequest shared exposed ports cause the wrong service to answer

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `serviceEndpointRequest` resolve a shared exposed port to the wrong service when multiple job-defined services expose similar ports?

## Target
- File/function: executors/kubernetes/service_proxy.go: serviceEndpointRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, shared port numbers across services
- Exploit idea: allow port identity to outrank service identity
- Invariant to test: service identity must remain stronger than shared port reuse
- Expected Immunefi impact: wrong-service access or secret exposure
- Fast validation: define similar service ports and verify explicit service identity wins
