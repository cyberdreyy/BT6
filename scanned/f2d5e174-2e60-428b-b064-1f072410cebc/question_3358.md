# Q3358: TerminalConnect proxy state remains live after cancellation

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `TerminalConnect` continue to serve or route bytes after the owning live job was canceled?

## Target
- File/function: executors/kubernetes/terminal.go: TerminalConnect
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, cancellation timing and reconnects
- Exploit idea: keep proxy state alive after cancellation should have ended it
- Invariant to test: proxy state must terminate when the live job is canceled
- Expected Immunefi impact: session hijack or post-job disclosure
- Fast validation: cancel a job and verify the proxy path shuts down immediately
