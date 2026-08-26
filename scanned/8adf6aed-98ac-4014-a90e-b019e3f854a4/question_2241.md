# Q2241: method routing selects a weaker handler in shard_endpoints.buildShardEndpoints

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests name a method at shard selection for a user trigger request routed to a sharded DON that `buildShardEndpoints` routes to a handler with weaker authorization while the payload targets a privileged capability?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/shard_endpoints.go](core/services/gateway/handlers/capabilities/v2/shard_endpoints.go) -> `buildShardEndpoints`
- Entrypoint: shard selection for a user trigger request routed to a sharded DON
- Attacker controls: donId/shard fields in the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `donId/shard fields in the request` with a mismatched method/payload pair.
- Invariant to test: method routing and payload authorization must be consistent
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: matrix test over method/payload mismatches
