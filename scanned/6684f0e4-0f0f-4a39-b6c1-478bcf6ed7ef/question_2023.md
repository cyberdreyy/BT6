# Q2023: grace window abused to alter the bundle in shard_endpoints.buildShardEndpoints

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit during the grace window handled by `buildShardEndpoints` at shard selection for a user trigger request routed to a sharded DON so the bundle returned to the user includes or omits responses chosen by the attacker?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/shard_endpoints.go](core/services/gateway/handlers/capabilities/v2/shard_endpoints.go) -> `buildShardEndpoints`
- Entrypoint: shard selection for a user trigger request routed to a sharded DON
- Attacker controls: donId/shard fields in the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `donId/shard fields in the request` against the grace deadline.
- Invariant to test: bundle composition must be determined by verified node responses only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test over bundle composition across grace timings
