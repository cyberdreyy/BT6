# Q1877: shard selection redirects execution in shard_endpoints.buildShardEndpoints

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests influence the shard/DON chosen by `buildShardEndpoints` at shard selection for a user trigger request routed to a sharded DON so their request executes on a shard that does not enforce the same authorization?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/shard_endpoints.go](core/services/gateway/handlers/capabilities/v2/shard_endpoints.go) -> `buildShardEndpoints`
- Entrypoint: shard selection for a user trigger request routed to a sharded DON
- Attacker controls: concurrent requests across shards (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `concurrent requests across shards` with crafted shard-selecting fields.
- Invariant to test: shard selection must not alter the authorization decision
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting identical authorization across shards
