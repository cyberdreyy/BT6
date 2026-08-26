# Q3261: name canonicalization collision in orm.FindBridges

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) register or reference a bridge/initiator name through `FindBridges` at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs that canonicalizes to an existing one (case, unicode, whitespace, length truncation), hijacking an existing job's data source?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `FindBridges`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: bridge name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Create `bridge name and URL` as a near-collision of an existing name.
- Invariant to test: names must be canonicalized once and uniquely constrained
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test creating near-collision names and asserting rejection
