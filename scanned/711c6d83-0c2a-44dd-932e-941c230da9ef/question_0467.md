# Q0467: caller-supplied request id in shard_endpoints.buildShardEndpoints

## Question
Does `buildShardEndpoints` at shard selection for a user trigger request routed to a sharded DON accept a caller-chosen request id, letting any internet client with an arbitrary externally-owned key sending signed gateway requests bind to or overwrite an in-flight request from another user?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/shard_endpoints.go](core/services/gateway/handlers/capabilities/v2/shard_endpoints.go) -> `buildShardEndpoints`
- Entrypoint: shard selection for a user trigger request routed to a sharded DON
- Attacker controls: concurrent requests across shards (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `concurrent requests across shards` reusing a victim's id.
- Invariant to test: request ids must be server-generated or sender-scoped
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test submitting a duplicate id from a different sender
