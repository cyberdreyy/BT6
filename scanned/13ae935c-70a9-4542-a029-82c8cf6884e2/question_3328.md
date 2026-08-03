# Q3328: proxyHTTPRequest proxy bytes mix across live sessions

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `proxyHTTPRequest` mix bytes from multiple live proxy or terminal sessions into one attacker-visible stream?

## Target
- File/function: executors/kubernetes/service_proxy.go: proxyHTTPRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, concurrent sessions and reconnect timing
- Exploit idea: lose byte ownership across live proxy sessions
- Invariant to test: proxy and terminal streams must preserve exact session ownership
- Expected Immunefi impact: secret exposure or output tampering
- Fast validation: open multiple sessions and verify no byte mixing occurs
