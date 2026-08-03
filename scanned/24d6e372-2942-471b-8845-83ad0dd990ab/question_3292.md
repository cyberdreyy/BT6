# Q3292: serviceEndpointRequest routing state from one session is reused by another

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `serviceEndpointRequest` reuse cached route, port, or target state from one session in another independent session?

## Target
- File/function: executors/kubernetes/service_proxy.go: serviceEndpointRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, multiple sessions and shared cache state
- Exploit idea: keep mutable routing state too globally scoped
- Invariant to test: session routing state must remain scoped to one live session
- Expected Immunefi impact: wrong-service access or cross-session disclosure
- Fast validation: open parallel sessions and verify route state is not shared
