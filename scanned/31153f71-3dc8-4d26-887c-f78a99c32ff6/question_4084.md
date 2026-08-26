# Q4084: initiator not bound to its job in cache.BridgeTypes

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) authenticate with one initiator's credential at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read and, through `BridgeTypes`, trigger runs for jobs bound to a different initiator?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `BridgeTypes`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: bridge name used as cache key (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `bridge name used as cache key` against another job's run endpoint.
- Invariant to test: an initiator may only trigger the jobs whose spec names it
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test triggering a foreign job with a valid EI credential
