# Q4272: bridge URL replaced under a live job in cache.BridgeTypes

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) update the bridge URL/token through `BridgeTypes` at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read so running jobs fetch observations from an attacker endpoint, changing the reported value?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `BridgeTypes`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: bridge name used as cache key (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Patch `bridge name used as cache key` to point at an attacker host.
- Invariant to test: bridge target changes must require admin authority and revalidate referencing jobs
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: integration test patching a bridge used by a live job
