# Q5045: token hashing weakness in orm.BridgeTypes

## Question
Is the incoming-token hash computed in `BridgeTypes` from bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs unsalted, truncated or reversible, letting an authenticated node user holding only the 'edit' role (non-admin) derive an accepted token from stored or leaked material?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `BridgeTypes`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: external initiator name (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Analyze `external initiator name` against the stored hash form.
- Invariant to test: tokens must be stored as salted, full-length hashes
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test asserting the hash construction and length
