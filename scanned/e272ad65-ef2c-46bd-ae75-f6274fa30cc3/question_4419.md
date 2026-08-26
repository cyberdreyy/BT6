# Q4419: expired entry cleanup races delivery in callback.Wait

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests time a request at the callback used to return a DON response to the originating gateway user so cleanup in `Wait` removes an entry mid-delivery and a later response is matched to the attacker's new request?

## Target
- File/function: [core/services/gateway/handlers/common/callback.go](core/services/gateway/handlers/common/callback.go) -> `Wait`
- Entrypoint: the callback used to return a DON response to the originating gateway user
- Attacker controls: timing of late responses (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `timing of late responses` against the expiry sweep.
- Invariant to test: cleanup and delivery must be mutually exclusive per entry
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test racing cleanup against delivery
