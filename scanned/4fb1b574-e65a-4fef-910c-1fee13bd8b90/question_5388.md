# Q5388: credential returned after creation in cache.CreateBridgeType

## Question
Does the create path through `CreateBridgeType` at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read return or persist the credential in a form readable later by an authenticated node user holding only the 'edit' role (non-admin) at a lower role?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `CreateBridgeType`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: update timing versus the cache refresh interval (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Create with `update timing versus the cache refresh interval`, then read the object back.
- Invariant to test: credentials are shown once and stored hashed
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: round-trip test asserting the secret is unreadable after creation
