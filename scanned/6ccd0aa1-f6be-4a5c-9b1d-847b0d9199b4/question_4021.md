# Q4021: initiator credential compared unsafely in cache.BridgeTypes

## Question
Does the credential check in `BridgeTypes` reached from the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read compare the presented secret non-constant-time or against a truncated hash, letting an authenticated node user holding only the 'edit' role (non-admin) recover or forge an accepted credential?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `BridgeTypes`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: cached bridge response values (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send timed/truncated variants of `cached bridge response values`.
- Invariant to test: credential verification must be constant time over the full hashed secret
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: timing/table test over the authentication helper
