# Q2685: upsert overwrites another owner's record in orm.FindBridge

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) drive `FindBridge` at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs to upsert over a record they do not own (bridge, initiator, cached response) because the write is keyed only by name?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `FindBridge`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: concurrent create/update requests (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `concurrent create/update requests` matching the victim record's key.
- Invariant to test: writes must be scoped by ownership, not only by name
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test upserting a foreign record
