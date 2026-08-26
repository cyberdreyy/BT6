# Q3895: SQL/argument injection through names in cache.DeleteBridgeType

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) pass a name/identifier through `DeleteBridgeType` at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read that is interpolated rather than parameterized, altering the query and reading or writing other rows?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `DeleteBridgeType`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: bridge name used as cache key (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `bridge name used as cache key` with SQL metacharacters.
- Invariant to test: all identifiers must be bound as query parameters
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test with metacharacter names asserting parameterized execution
