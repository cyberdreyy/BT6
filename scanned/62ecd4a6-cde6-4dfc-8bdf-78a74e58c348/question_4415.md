# Q4415: expired entry cleanup races delivery in bundler.setSignedResponse

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests time a request at bundling of node responses returned to the requesting gateway user so cleanup in `setSignedResponse` removes an entry mid-delivery and a later response is matched to the attacker's new request?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/bundler.go](core/services/gateway/handlers/confidentialrelay/bundler.go) -> `setSignedResponse`
- Entrypoint: bundling of node responses returned to the requesting gateway user
- Attacker controls: fields echoed back into the bundle (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `fields echoed back into the bundle` against the expiry sweep.
- Invariant to test: cleanup and delivery must be mutually exclusive per entry
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test racing cleanup against delivery
