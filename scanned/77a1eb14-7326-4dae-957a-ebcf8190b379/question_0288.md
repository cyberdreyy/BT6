# Q0288: token hashing weakness in bridge_type.NewBridgeType

## Question
Is the incoming-token hash computed in `NewBridgeType` from bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs unsalted, truncated or reversible, letting a holder of an external-initiator access-key/secret pair derive an accepted token from stored or leaked material?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `NewBridgeType`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: the incoming access key/token (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Analyze `incoming access key/token` against the stored hash form.
- Invariant to test: tokens must be stored as salted, full-length hashes
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test asserting the hash construction and length
