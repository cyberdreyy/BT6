# Q1721: metadata sync race grants access in shard_endpoints.buildShardEndpoints

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit at shard selection for a user trigger request routed to a sharded DON during the metadata refresh handled by `buildShardEndpoints` so authorization is evaluated against empty or stale metadata and defaults to allow?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/shard_endpoints.go](core/services/gateway/handlers/capabilities/v2/shard_endpoints.go) -> `buildShardEndpoints`
- Entrypoint: shard selection for a user trigger request routed to a sharded DON
- Attacker controls: workflow identifiers that select a shard (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `workflow identifiers that select a shard` against the sync tick.
- Invariant to test: authorization must fail closed while metadata is unavailable or stale
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test submitting during a metadata gap and asserting rejection
