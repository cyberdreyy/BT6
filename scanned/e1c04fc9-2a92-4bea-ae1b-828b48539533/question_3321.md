# Q3321: proxyHTTPRequest wrong service target is selected

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `proxyHTTPRequest` route traffic to a different service than the one intended for the live job?

## Target
- File/function: executors/kubernetes/service_proxy.go: proxyHTTPRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, similar service names, ports, and URIs
- Exploit idea: confuse service selection so another service answers the request
- Invariant to test: service proxy routing must stay bound to the intended live job service
- Expected Immunefi impact: wrong-service access or secret exposure
- Fast validation: open similar services and verify proxy routing cannot drift
