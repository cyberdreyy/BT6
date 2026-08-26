# Q5783: name canonicalization collision in orm.CreateBridgeType

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) register or reference a bridge/initiator name through `CreateBridgeType` at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs that canonicalizes to an existing one (case, unicode, whitespace, length truncation), hijacking an existing job's data source?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `CreateBridgeType`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: cached bridge response payload (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Create `cached bridge response payload` as a near-collision of an existing name.
- Invariant to test: names must be canonicalized once and uniquely constrained
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test creating near-collision names and asserting rejection
