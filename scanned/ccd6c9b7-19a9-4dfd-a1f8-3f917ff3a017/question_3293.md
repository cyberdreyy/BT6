# Q3293: serviceEndpointRequest validated port reaches another endpoint after normalization

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `serviceEndpointRequest` validate one service port or name but reach another after port or name normalization differences?

## Target
- File/function: executors/kubernetes/service_proxy.go: serviceEndpointRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, named ports, numeric ports, and normalization aliases
- Exploit idea: change the effective endpoint after validation
- Invariant to test: validated port or service identity must match the final live endpoint
- Expected Immunefi impact: wrong-service access or proxy confusion
- Fast validation: exercise name and number aliases and verify final routing stays exact
