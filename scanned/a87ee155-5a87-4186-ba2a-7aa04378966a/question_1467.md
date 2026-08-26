# Q1467: bridge URL replaced under a live job in orm.transact

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) update the bridge URL/token through `transact` at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs so running jobs fetch observations from an attacker endpoint, changing the reported value?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `transact`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: cached bridge response payload (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Patch `cached bridge response payload` to point at an attacker host.
- Invariant to test: bridge target changes must require admin authority and revalidate referencing jobs
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test patching a bridge used by a live job
