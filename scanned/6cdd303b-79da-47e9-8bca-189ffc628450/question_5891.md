# Q5891: job spec references another owner's credential in csa_keys_controller.Import

## Question
Can an authenticated node user holding only the 'view' role create or update a job through `Import` at /v2/keys/csa and /v2/keys/csa/export/:ID that references a bridge, initiator or key belonging to someone else, causing the node to use that credential on the attacker's behalf?

## Target
- File/function: [core/web/csa_keys_controller.go](core/web/csa_keys_controller.go) -> `Import`
- Entrypoint: /v2/keys/csa and /v2/keys/csa/export/:ID
- Attacker controls: the export password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `export password` referencing the foreign object by name.
- Invariant to test: specs may only reference objects the submitter is entitled to use
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test submitting a spec referencing a foreign credential
