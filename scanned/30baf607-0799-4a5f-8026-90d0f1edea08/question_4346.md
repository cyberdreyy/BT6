# Q4346: in-flight request map keyed without sender in handler.sendHTTPMessageToClient

## Question
Is the in-flight request map used by `sendHTTPMessageToClient` at web-API trigger and outgoing-request handling on the public gateway user endpoint keyed without the authenticated sender, letting any internet client with an arbitrary externally-owned key sending signed gateway requests evict, complete or read another user's entry?

## Target
- File/function: [core/services/gateway/handlers/capabilities/handler.go](core/services/gateway/handlers/capabilities/handler.go) -> `sendHTTPMessageToClient`
- Entrypoint: web-API trigger and outgoing-request handling on the public gateway user endpoint
- Attacker controls: messageId used for callback correlation (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `messageId used for callback correlation` colliding with the victim's key.
- Invariant to test: in-flight state must be namespaced by verified sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting cross-sender key isolation
