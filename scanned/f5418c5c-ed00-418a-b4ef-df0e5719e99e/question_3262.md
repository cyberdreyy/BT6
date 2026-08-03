# Q3262: ProxyRequest websocket and HTTP paths diverge to different services

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `ProxyRequest` handle WebSocket and HTTP variants of the same request through different target services?

## Target
- File/function: executors/kubernetes/service_proxy.go: ProxyRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, HTTP versus WebSocket requests and shared URIs
- Exploit idea: split routing logic so upgrades and plain requests hit different services
- Invariant to test: all variants of one logical proxy request must target the same service
- Expected Immunefi impact: wrong-service access or session confusion
- Fast validation: probe both upgrade and non-upgrade paths and verify they resolve to the same target
