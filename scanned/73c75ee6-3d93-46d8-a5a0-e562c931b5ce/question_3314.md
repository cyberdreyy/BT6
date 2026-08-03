# Q3314: proxyWSRequest terminal settings leak across sessions

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `proxyWSRequest` reuse terminal settings or live-session metadata from one job session in another session?

## Target
- File/function: executors/kubernetes/service_proxy.go: proxyWSRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, terminal settings and overlapping sessions
- Exploit idea: cross-bind mutable terminal state across live sessions
- Invariant to test: terminal metadata must remain scoped to the owning session
- Expected Immunefi impact: cross-session confusion or disclosure
- Fast validation: open sequential sessions and verify terminal settings do not leak across them
