# Q4348: in-flight request map keyed without sender in http_trigger_handler.HandleUserTriggerRequest

## Question
Is the in-flight request map used by `HandleUserTriggerRequest` at HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger) keyed without the authenticated sender, letting any internet client with an arbitrary externally-owned key sending signed gateway requests evict, complete or read another user's entry?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go](core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go) -> `HandleUserTriggerRequest`
- Entrypoint: HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger)
- Attacker controls: requestID/key and its derivation inputs (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `requestID/key and its derivation inputs` colliding with the victim's key.
- Invariant to test: in-flight state must be namespaced by verified sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting cross-sender key isolation
