# Q2803: run triggered on a job the caller cannot access in bridge_types_controller.ValidateBridgeType

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) trigger execution through `ValidateBridgeType` at POST/PATCH/GET /v2/bridge_types for a job they were not granted, injecting attacker-chosen input into an oracle report?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `ValidateBridgeType`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: incoming/outgoing token fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `incoming/outgoing token fields` naming another job's id with an attacker payload.
- Invariant to test: run triggering must be bound to the caller's entitlement for that exact job
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test triggering a foreign job and asserting rejection
