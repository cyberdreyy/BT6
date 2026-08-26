# Q3705: credential returned after creation in orm.FindBridges

## Question
Does the create path through `FindBridges` at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs return or persist the credential in a form readable later by an authenticated node user holding only the 'edit' role (non-admin) at a lower role?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `FindBridges`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: concurrent create/update requests (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Create with `concurrent create/update requests`, then read the object back.
- Invariant to test: credentials are shown once and stored hashed
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: round-trip test asserting the secret is unreadable after creation
