# Q2004: transaction boundary leaves half-applied state in orm.transact

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) abort mid-write at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs so `transact` leaves a record whose credential is unset but which still authenticates or resolves?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `transact`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: external initiator name (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Interrupt `external initiator name` between the writes.
- Invariant to test: record creation must be atomic with its credential
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test asserting no record exists without its credential
