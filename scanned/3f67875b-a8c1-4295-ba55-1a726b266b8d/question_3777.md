# Q3777: message id chosen by the caller in multihandler.HandleJSONRPCUserMessage

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests choose a message/request id at gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests that collides with another user's in-flight request tracked via `HandleJSONRPCUserMessage`, so responses are cross-delivered?

## Target
- File/function: [core/services/gateway/multihandler.go](core/services/gateway/multihandler.go) -> `HandleJSONRPCUserMessage`
- Entrypoint: gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests
- Attacker controls: donId selection (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `donId selection` reusing a victim's id.
- Invariant to test: request identity must include the authenticated sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test issuing two requests with the same id from different senders
