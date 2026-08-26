# Q0745: resume/callback path unauthenticated or unbound in csa_keys_controller.Index

## Question
Can an authenticated node user holding only the 'view' role resume or complete a pending run through `Index` at /v2/keys/csa and /v2/keys/csa/export/:ID by guessing or reusing a run identifier, injecting the final value?

## Target
- File/function: [core/web/csa_keys_controller.go](core/web/csa_keys_controller.go) -> `Index`
- Entrypoint: /v2/keys/csa and /v2/keys/csa/export/:ID
- Attacker controls: the key ID path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `key ID path parameter` with an enumerated run id and chosen payload.
- Invariant to test: run resume must require an unguessable, single-use, run-bound token
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test resuming another run with a guessed identifier
