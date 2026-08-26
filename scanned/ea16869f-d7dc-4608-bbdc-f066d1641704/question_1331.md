# Q1331: error response discloses secret material in shard_endpoints.buildShardEndpoints

## Question
Do error paths in `buildShardEndpoints` at shard selection for a user trigger request routed to a sharded DON include node responses, partial plaintext or key identifiers that reveal secret material to any internet client with an arbitrary externally-owned key sending signed gateway requests?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/shard_endpoints.go](core/services/gateway/handlers/capabilities/v2/shard_endpoints.go) -> `buildShardEndpoints`
- Entrypoint: shard selection for a user trigger request routed to a sharded DON
- Attacker controls: donId/shard fields in the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force partial failure with `donId/shard fields in the request`.
- Invariant to test: error paths must not carry node payloads to the user
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting error payloads exclude node data
