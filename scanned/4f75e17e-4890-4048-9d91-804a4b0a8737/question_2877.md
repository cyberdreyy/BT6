# Q2877: concurrent create defeats uniqueness in orm.FindBridge

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) race creations through `FindBridge` at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs to obtain two records with the same effective name, so job resolution becomes attacker-controlled?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `FindBridge`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: cached bridge response payload (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `cached bridge response payload`.
- Invariant to test: uniqueness must be enforced by a DB constraint inside the transaction
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: concurrent test asserting a single row survives
