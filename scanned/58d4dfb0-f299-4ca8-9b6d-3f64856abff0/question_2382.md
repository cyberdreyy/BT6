# Q2382: expired entry cleanup races delivery in bundler.addError

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests time a request at bundling of node responses returned to the requesting gateway user so cleanup in `addError` removes an entry mid-delivery and a later response is matched to the attacker's new request?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/bundler.go](core/services/gateway/handlers/confidentialrelay/bundler.go) -> `addError`
- Entrypoint: bundling of node responses returned to the requesting gateway user
- Attacker controls: the request that determines bundle composition (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `request that determines bundle composition` against the expiry sweep.
- Invariant to test: cleanup and delivery must be mutually exclusive per entry
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test racing cleanup against delivery
