# Q3343: TerminalConnect session state survives after job end

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `TerminalConnect` leave proxy or terminal session state alive after the live job has already ended?

## Target
- File/function: executors/kubernetes/terminal.go: TerminalConnect
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, rapid job turnover and reconnects
- Exploit idea: keep live session state beyond the owning job lifetime
- Invariant to test: session and proxy state must terminate with the live job
- Expected Immunefi impact: session hijack or cross-job disclosure
- Fast validation: end a job and verify proxy or terminal state cannot be reused
