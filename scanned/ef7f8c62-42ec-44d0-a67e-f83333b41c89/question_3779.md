# Q3779: message id chosen by the caller in utils.StringToAlignedBytes

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests choose a message/request id at the encoding/signing helpers used on every gateway message before authorization that collides with another user's in-flight request tracked via `StringToAlignedBytes`, so responses are cross-delivered?

## Target
- File/function: [core/services/gateway/common/utils.go](core/services/gateway/common/utils.go) -> `StringToAlignedBytes`
- Entrypoint: the encoding/signing helpers used on every gateway message before authorization
- Attacker controls: signature bytes passed to ExtractSigner (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `signature bytes passed to ExtractSigner` reusing a victim's id.
- Invariant to test: request identity must include the authenticated sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test issuing two requests with the same id from different senders
