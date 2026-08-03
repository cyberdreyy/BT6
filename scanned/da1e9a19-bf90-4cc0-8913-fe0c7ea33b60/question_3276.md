# Q3276: ProxyRequest service recreation races the proxy target

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `ProxyRequest` keep routing toward a service name that was deleted and recreated with attacker-controlled backing state?

## Target
- File/function: executors/kubernetes/service_proxy.go: ProxyRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, service recreation and route caching
- Exploit idea: reuse route state across service recreation boundaries
- Invariant to test: proxy target identity must follow the current service instance, not just the name
- Expected Immunefi impact: wrong-service access or stale-state trust
- Fast validation: recreate services and verify routing state does not survive incorrectly
