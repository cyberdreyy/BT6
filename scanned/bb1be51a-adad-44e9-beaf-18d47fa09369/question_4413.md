# Q4413: expired entry cleanup races delivery in shard_endpoints.allMembers

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests time a request at shard selection for a user trigger request routed to a sharded DON so cleanup in `allMembers` removes an entry mid-delivery and a later response is matched to the attacker's new request?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/shard_endpoints.go](core/services/gateway/handlers/capabilities/v2/shard_endpoints.go) -> `allMembers`
- Entrypoint: shard selection for a user trigger request routed to a sharded DON
- Attacker controls: concurrent requests across shards (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `concurrent requests across shards` against the expiry sweep.
- Invariant to test: cleanup and delivery must be mutually exclusive per entry
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test racing cleanup against delivery
