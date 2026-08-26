# Q5386: credential returned after creation in bridge_type.MustParseBridgeName

## Question
Does the create path through `MustParseBridgeName` at bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs return or persist the credential in a form readable later by a holder of an external-initiator access-key/secret pair at a lower role?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `MustParseBridgeName`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: bridge name string (case, unicode, length) (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Create with `bridge name string (case, unicode, length)`, then read the object back.
- Invariant to test: credentials are shown once and stored hashed
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: round-trip test asserting the secret is unreadable after creation
