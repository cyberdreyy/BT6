# Q2429: token hashing weakness in cache.FindBridges

## Question
Is the incoming-token hash computed in `FindBridges` from the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read unsalted, truncated or reversible, letting an authenticated node user holding only the 'edit' role (non-admin) derive an accepted token from stored or leaked material?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `FindBridges`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: update timing versus the cache refresh interval (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Analyze `update timing versus the cache refresh interval` against the stored hash form.
- Invariant to test: tokens must be stored as salted, full-length hashes
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test asserting the hash construction and length
