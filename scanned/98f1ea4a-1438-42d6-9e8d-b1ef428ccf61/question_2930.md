# Q2930: resume/callback path unauthenticated or unbound in external_initiators_controller.Index

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) resume or complete a pending run through `Index` at POST/DELETE /v2/external_initiators by guessing or reusing a run identifier, injecting the final value?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `Index`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: the initiator name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `initiator name and URL` with an enumerated run id and chosen payload.
- Invariant to test: run resume must require an unguessable, single-use, run-bound token
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test resuming another run with a guessed identifier
