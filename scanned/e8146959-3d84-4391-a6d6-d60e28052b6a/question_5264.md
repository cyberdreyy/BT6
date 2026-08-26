# Q5264: profiling endpoint yields key material in external_initiators_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) obtain a heap/goroutine profile through `Create` at POST/DELETE /v2/external_initiators containing in-memory private keys, passwords or session tokens?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `Create`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: duplicate/colliding names (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `duplicate/colliding names` against the profiling handler and scan the dump.
- Invariant to test: profiling endpoints must be admin-only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test fetching a profile from a low-role session and asserting 403
