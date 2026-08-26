# Q3958: meta/data blob echoed into signed output in cache.DeleteBridgeType

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) place attacker data in a bridge/initiator meta field through `DeleteBridgeType` at the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read that reaches the value the node signs and reports on-chain?

## Target
- File/function: [core/bridges/cache.go](core/bridges/cache.go) -> `DeleteBridgeType`
- Entrypoint: the bridge cache consulted on every bridge-backed pipeline run and /v2/bridge_types read
- Attacker controls: update timing versus the cache refresh interval (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `update timing versus the cache refresh interval` with crafted meta content.
- Invariant to test: externally supplied meta must not influence the reported observation
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: pipeline test asserting the report is independent of meta content
