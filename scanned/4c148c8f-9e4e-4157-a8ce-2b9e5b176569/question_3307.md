# Q3307: proxyWSRequest similar service names collide

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `proxyWSRequest` collapse similar live service names or selectors into one proxied target?

## Target
- File/function: executors/kubernetes/service_proxy.go: proxyWSRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, similar service names and selectors
- Exploit idea: make service identity non-unique at routing time
- Invariant to test: service identity must remain exact for the live job
- Expected Immunefi impact: wrong-service access or cross-service confusion
- Fast validation: use similar service names and verify the proxy stays exact
