# Q1623: cached response reused across requests in orm.transact

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) cause the cached bridge response handled by `transact` at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs to be served to a different job or run, substituting attacker data into another job's observation?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `transact`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: bridge name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `bridge name and URL` that collides with another entry's cache key.
- Invariant to test: cache keys must include every field that determines correctness
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test asserting cache-key uniqueness across jobs/params
