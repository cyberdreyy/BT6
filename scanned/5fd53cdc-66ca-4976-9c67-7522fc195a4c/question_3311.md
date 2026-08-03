# Q3311: proxyWSRequest lower-trust session state reaches a protected job

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `proxyWSRequest` reuse session or routing state from an unprotected or unrelated job after a trust-boundary change?

## Target
- File/function: executors/kubernetes/service_proxy.go: proxyWSRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, protected and unprotected jobs plus shared session state
- Exploit idea: carry live session state across a protection boundary
- Invariant to test: session and routing state must be isolated per job trust boundary
- Expected Immunefi impact: protected-job session hijack or disclosure
- Fast validation: seed session state from an unprotected job and verify a protected job never inherits it
