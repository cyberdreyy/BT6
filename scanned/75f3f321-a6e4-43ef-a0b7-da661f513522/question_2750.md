# Q2750: deserialization accepts hostile fields in cache.FindBridges

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) submit a payload at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read whose unmarshalling in `FindBridges` sets fields the API does not expose (id, owner, token, created_at), taking over an existing record?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `FindBridges`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: bridge name used as cache key (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Include `bridge name used as cache key` with extra JSON fields.
- Invariant to test: unmarshalling must reject unknown and server-owned fields
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test posting bodies with server-owned fields
