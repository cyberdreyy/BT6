# Q2932: resume/callback path unauthenticated or unbound in keys_controller.Create

## Question
Can an authenticated node user holding only the 'view' role resume or complete a pending run through `Create` at /v2/keys/:keyType Index/Export/Import/Delete routes by guessing or reusing a run identifier, injecting the final value?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Create`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the keyType path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `keyType path parameter` with an enumerated run id and chosen payload.
- Invariant to test: run resume must require an unguessable, single-use, run-bound token
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test resuming another run with a guessed identifier
