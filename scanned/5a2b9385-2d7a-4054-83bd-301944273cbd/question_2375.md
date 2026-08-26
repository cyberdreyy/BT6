# Q2375: expired entry cleanup races delivery in handler.NewHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests time a request at web-API trigger and outgoing-request handling on the public gateway user endpoint so cleanup in `NewHandler` removes an entry mid-delivery and a later response is matched to the attacker's new request?

## Target
- File/function: [core/services/gateway/handlers/capabilities/handler.go](core/services/gateway/handlers/capabilities/handler.go) -> `NewHandler`
- Entrypoint: web-API trigger and outgoing-request handling on the public gateway user endpoint
- Attacker controls: target URL/headers of the outgoing HTTP request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `target URL/headers of the outgoing HTTP request` against the expiry sweep.
- Invariant to test: cleanup and delivery must be mutually exclusive per entry
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test racing cleanup against delivery
