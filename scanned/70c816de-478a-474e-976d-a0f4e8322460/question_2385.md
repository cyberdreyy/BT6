# Q2385: expired entry cleanup races delivery in requestcache.NewRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests time a request at the gateway request cache keyed per user request so cleanup in `NewRequest` removes an entry mid-delivery and a later response is matched to the attacker's new request?

## Target
- File/function: [core/services/gateway/handlers/common/requestcache.go](core/services/gateway/handlers/common/requestcache.go) -> `NewRequest`
- Entrypoint: the gateway request cache keyed per user request
- Attacker controls: the request id/key fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `request id/key fields` against the expiry sweep.
- Invariant to test: cleanup and delivery must be mutually exclusive per entry
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test racing cleanup against delivery
