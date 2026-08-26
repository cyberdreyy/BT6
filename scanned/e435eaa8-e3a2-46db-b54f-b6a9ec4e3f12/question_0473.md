# Q0473: caller-supplied request id in callback.SendResponse

## Question
Does `SendResponse` at the callback used to return a DON response to the originating gateway user accept a caller-chosen request id, letting any internet client with an arbitrary externally-owned key sending signed gateway requests bind to or overwrite an in-flight request from another user?

## Target
- File/function: [core/services/gateway/handlers/common/callback.go](core/services/gateway/handlers/common/callback.go) -> `SendResponse`
- Entrypoint: the callback used to return a DON response to the originating gateway user
- Attacker controls: timing of late responses (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `timing of late responses` reusing a victim's id.
- Invariant to test: request ids must be server-generated or sender-scoped
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test submitting a duplicate id from a different sender
