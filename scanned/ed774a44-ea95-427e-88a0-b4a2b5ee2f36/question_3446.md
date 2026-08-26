# Q3446: profiling endpoint yields key material in csa_keys_controller.Create

## Question
Can an authenticated node user holding only the 'view' role obtain a heap/goroutine profile through `Create` at /v2/keys/csa and /v2/keys/csa/export/:ID containing in-memory private keys, passwords or session tokens?

## Target
- File/function: [core/web/csa_keys_controller.go](core/web/csa_keys_controller.go) -> `Create`
- Entrypoint: /v2/keys/csa and /v2/keys/csa/export/:ID
- Attacker controls: imported key material (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `imported key material` against the profiling handler and scan the dump.
- Invariant to test: profiling endpoints must be admin-only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test fetching a profile from a low-role session and asserting 403
