# Q3355: TerminalConnect websocket upgrade state binds to the wrong target

## Question
Can an unprivileged GitLab user or pipeline author enter through interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job and make `TerminalConnect` upgrade one proxied connection using routing state that belongs to another target?

## Target
- File/function: executors/kubernetes/terminal.go: TerminalConnect
- Entrypoint: interactive terminal or service proxy access reachable from a live unprivileged Kubernetes job
- Attacker controls: requested URI, reconnect timing, live job state, and service definitions from the job, upgrade timing and competing targets
- Exploit idea: reuse stale routing state during WebSocket upgrade
- Invariant to test: upgrade handling must stay bound to one live target
- Expected Immunefi impact: wrong-service access or session hijack
- Fast validation: trigger competing upgrades and verify target binding stays exact
