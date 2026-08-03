# Q3306: proxyWSRequest requested URI reaches an unauthorized endpoint

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `proxyWSRequest` preserve attacker-controlled request paths strongly enough that the proxy reaches an unintended internal endpoint for the live service?

## Target
- File/function: executors/kubernetes/service_proxy.go: proxyWSRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, requested URIs and path components
- Exploit idea: abuse path handling to reach a different internal endpoint than intended
- Invariant to test: proxied request paths must stay within the intended live service surface
- Expected Immunefi impact: wrong-service access or internal endpoint exposure
- Fast validation: probe crafted request paths and verify proxy confinement
