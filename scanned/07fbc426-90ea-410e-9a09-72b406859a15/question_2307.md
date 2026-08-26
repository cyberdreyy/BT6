# Q2307: in-flight request map keyed without sender in webapi.Validate

## Question
Is the in-flight request map used by `Validate` at the web-API capability handler config validation and request path from the public gateway endpoint keyed without the authenticated sender, letting any internet client with an arbitrary externally-owned key sending signed gateway requests evict, complete or read another user's entry?

## Target
- File/function: [core/services/gateway/handlers/capabilities/webapi.go](core/services/gateway/handlers/capabilities/webapi.go) -> `Validate`
- Entrypoint: the web-API capability handler config validation and request path from the public gateway endpoint
- Attacker controls: rate/quota-relevant fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `rate/quota-relevant fields` colliding with the victim's key.
- Invariant to test: in-flight state must be namespaced by verified sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting cross-sender key isolation
