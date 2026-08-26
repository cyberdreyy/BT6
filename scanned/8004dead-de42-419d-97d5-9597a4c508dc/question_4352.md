# Q4352: in-flight request map keyed without sender in handler.copiedResponses

## Question
Is the in-flight request map used by `copiedResponses` at HandleJSONRPCUserMessage on the confidential-relay gateway method keyed without the authenticated sender, letting any internet client with an arbitrary externally-owned key sending signed gateway requests evict, complete or read another user's entry?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/handler.go](core/services/gateway/handlers/confidentialrelay/handler.go) -> `copiedResponses`
- Entrypoint: HandleJSONRPCUserMessage on the confidential-relay gateway method
- Attacker controls: requestID used to key the active request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `requestID used to key the active request` colliding with the victim's key.
- Invariant to test: in-flight state must be namespaced by verified sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting cross-sender key isolation
