# Q3339: proxyHTTPRequest one job’s live request shapes another session

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `proxyHTTPRequest` reuse request-derived routing or state from one job when serving another live session?

## Target
- File/function: executors/kubernetes/service_proxy.go: proxyHTTPRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, overlapping jobs and mutable request state
- Exploit idea: hold request-derived state too globally across sessions
- Invariant to test: per-session request state must remain isolated by job identity
- Expected Immunefi impact: cross-job session confusion or disclosure
- Fast validation: issue overlapping session requests and verify no routing state crosses jobs
