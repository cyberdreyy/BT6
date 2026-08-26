# Q1622: cached response reused across requests in bridge_type.AuthenticateBridgeType

## Question
Can a holder of an external-initiator access-key/secret pair cause the cached bridge response handled by `AuthenticateBridgeType` at bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs to be served to a different job or run, substituting attacker data into another job's observation?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `AuthenticateBridgeType`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: the bridge JSON body (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `bridge JSON body` that collides with another entry's cache key.
- Invariant to test: cache keys must include every field that determines correctness
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test asserting cache-key uniqueness across jobs/params
