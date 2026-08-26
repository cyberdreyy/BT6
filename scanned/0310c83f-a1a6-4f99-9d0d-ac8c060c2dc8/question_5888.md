# Q5888: job spec references another owner's credential in bridge_types_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) create or update a job through `Create` at POST/PATCH/GET /v2/bridge_types that references a bridge, initiator or key belonging to someone else, causing the node to use that credential on the attacker's behalf?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `Create`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: incoming/outgoing token fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `incoming/outgoing token fields` referencing the foreign object by name.
- Invariant to test: specs may only reference objects the submitter is entitled to use
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test submitting a spec referencing a foreign credential
