# Q0746: resume/callback path unauthenticated or unbound in dkg_recipient_keys_controller.Index

## Question
Can an authenticated node user holding only the 'view' role resume or complete a pending run through `Index` at GET /v2/keys/dkgrecipient by guessing or reusing a run identifier, injecting the final value?

## Target
- File/function: [core/web/dkg_recipient_keys_controller.go](core/web/dkg_recipient_keys_controller.go) -> `Index`
- Entrypoint: GET /v2/keys/dkgrecipient
- Attacker controls: selected response fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `selected response fields` with an enumerated run id and chosen payload.
- Invariant to test: run resume must require an unguessable, single-use, run-bound token
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test resuming another run with a guessed identifier
