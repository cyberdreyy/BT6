# Q3441: profiling endpoint yields key material in jobs_controller.Show

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) obtain a heap/goroutine profile through `Show` at POST/PATCH /v2/jobs (edit role) containing in-memory private keys, passwords or session tokens?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Show`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: update payload on an existing job (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `update payload on an existing job` against the profiling handler and scan the dump.
- Invariant to test: profiling endpoints must be admin-only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test fetching a profile from a low-role session and asserting 403
