# Q0309: length/format validation gap in shard_endpoints.buildShardEndpoints

## Question
Does the field validation in `buildShardEndpoints` at shard selection for a user trigger request routed to a sharded DON accept an over-long, truncated or non-hex identifier that later code slices or parses unchecked, letting any internet client with an arbitrary externally-owned key sending signed gateway requests address a different workflow?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/shard_endpoints.go](core/services/gateway/handlers/capabilities/v2/shard_endpoints.go) -> `buildShardEndpoints`
- Entrypoint: shard selection for a user trigger request routed to a sharded DON
- Attacker controls: workflow identifiers that select a shard (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `workflow identifiers that select a shard` at and beyond the documented length bounds.
- Invariant to test: every identifier must be length- and charset-validated before use
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test at length boundaries for each identifier field
