# Q0461: caller-supplied request id in webapi.Validate

## Question
Does `Validate` at the web-API capability handler config validation and request path from the public gateway endpoint accept a caller-chosen request id, letting any internet client with an arbitrary externally-owned key sending signed gateway requests bind to or overwrite an in-flight request from another user?

## Target
- File/function: [core/services/gateway/handlers/capabilities/webapi.go](core/services/gateway/handlers/capabilities/webapi.go) -> `Validate`
- Entrypoint: the web-API capability handler config validation and request path from the public gateway endpoint
- Attacker controls: rate/quota-relevant fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `rate/quota-relevant fields` reusing a victim's id.
- Invariant to test: request ids must be server-generated or sender-scoped
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test submitting a duplicate id from a different sender
