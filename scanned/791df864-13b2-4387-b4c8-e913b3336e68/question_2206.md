# Q2206: job spec references another owner's credential in external_initiators_controller.ValidateExternalInitiator

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) create or update a job through `ValidateExternalInitiator` at POST/DELETE /v2/external_initiators that references a bridge, initiator or key belonging to someone else, causing the node to use that credential on the attacker's behalf?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `ValidateExternalInitiator`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: returned credential fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `returned credential fields` referencing the foreign object by name.
- Invariant to test: specs may only reference objects the submitter is entitled to use
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test submitting a spec referencing a foreign credential
