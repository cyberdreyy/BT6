# Q3448: profiling endpoint yields key material in evm_transfer_controller.CreateWithRelayer

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) obtain a heap/goroutine profile through `CreateWithRelayer` at POST /v2/transfers/evm containing in-memory private keys, passwords or session tokens?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateWithRelayer`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: gas limit and token contract fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `gas limit and token contract fields` against the profiling handler and scan the dump.
- Invariant to test: profiling endpoints must be admin-only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test fetching a profile from a low-role session and asserting 403
