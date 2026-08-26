# Q1379: profiling endpoint yields key material in log_controller.Patch

## Question
Can an authenticated node user holding only the 'view' role obtain a heap/goroutine profile through `Patch` at GET and PATCH /v2/log containing in-memory private keys, passwords or session tokens?

## Target
- File/function: [core/web/log_controller.go](core/web/log_controller.go) -> `Patch`
- Entrypoint: GET and PATCH /v2/log
- Attacker controls: repeated toggling of SQL logging (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `repeated toggling of SQL logging` against the profiling handler and scan the dump.
- Invariant to test: profiling endpoints must be admin-only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test fetching a profile from a low-role session and asserting 403
