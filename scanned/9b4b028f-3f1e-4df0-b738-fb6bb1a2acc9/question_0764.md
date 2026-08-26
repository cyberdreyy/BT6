# Q0764: credential returned after creation in cache.NewCache

## Question
Does the create path through `NewCache` at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read return or persist the credential in a form readable later by an authenticated node user holding only the 'edit' role (non-admin) at a lower role?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `NewCache`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: bridge name used as cache key (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Create with `bridge name used as cache key`, then read the object back.
- Invariant to test: credentials are shown once and stored hashed
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: round-trip test asserting the secret is unreadable after creation
