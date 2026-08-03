# Q3310: proxyWSRequest service outputs leak through the live session boundary

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `proxyWSRequest` carry service-side secrets or outputs through a proxy or terminal path visible to the attacker-controlled job?

## Target
- File/function: executors/kubernetes/service_proxy.go: proxyWSRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, service outputs, terminal access, and proxied bytes
- Exploit idea: cross the boundary between service-private output and attacker-visible session output
- Invariant to test: proxy and terminal boundaries must not leak higher-trust service output
- Expected Immunefi impact: secret exposure across job roles
- Fast validation: emit sensitive output from services and verify it is not exposed to attacker-visible sessions
