# Q3269: ProxyRequest terminal reconnect attaches to a later job

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `ProxyRequest` reconnect a terminal to a different live job than the one that originated it?

## Target
- File/function: executors/kubernetes/service_proxy.go: ProxyRequest
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, reconnect timing and overlapping jobs
- Exploit idea: hold terminal identity too loosely across job turnover
- Invariant to test: terminal identity must stay bound to the originating live job
- Expected Immunefi impact: session hijack or unauthorized job access
- Fast validation: turn jobs over quickly and verify reconnects cannot switch job identity
