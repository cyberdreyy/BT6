# Q3264: ProxyRequest proxy remains bound to a stale pod after restart

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `ProxyRequest` keep routing to an old pod or service instance after the live workload was replaced?

## Target
- File/function: executors/kubernetes/service_proxy.go: ProxyRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, pod restarts, service replacement, and reconnects
- Exploit idea: reuse stale target state after workload replacement
- Invariant to test: proxy target identity must follow the current live workload only
- Expected Immunefi impact: wrong-service access or stale-session hijack
- Fast validation: restart the live workload and verify proxy routing rebinds safely
