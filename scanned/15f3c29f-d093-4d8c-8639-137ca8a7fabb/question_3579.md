# Q3579: upsert overwrites another owner's record in orm.FindBridges

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) drive `FindBridges` at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs to upsert over a record they do not own (bridge, initiator, cached response) because the write is keyed only by name?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `FindBridges`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: external initiator name (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `external initiator name` matching the victim record's key.
- Invariant to test: writes must be scoped by ownership, not only by name
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test upserting a foreign record
