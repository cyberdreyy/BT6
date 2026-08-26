# Q2076: SQL/argument injection through names in bridge_type.AuthenticateBridgeType

## Question
Can a holder of an external-initiator access-key/secret pair pass a name/identifier through `AuthenticateBridgeType` at bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs that is interpolated rather than parameterized, altering the query and reading or writing other rows?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `AuthenticateBridgeType`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: the bridge JSON body (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `bridge JSON body` with SQL metacharacters.
- Invariant to test: all identifiers must be bound as query parameters
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test with metacharacter names asserting parameterized execution
