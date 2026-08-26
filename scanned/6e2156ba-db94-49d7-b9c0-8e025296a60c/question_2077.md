# Q2077: SQL/argument injection through names in orm.transact

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) pass a name/identifier through `transact` at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs that is interpolated rather than parameterized, altering the query and reading or writing other rows?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `transact`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: cached bridge response payload (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `cached bridge response payload` with SQL metacharacters.
- Invariant to test: all identifiers must be bound as query parameters
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test with metacharacter names asserting parameterized execution
