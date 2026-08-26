# Q5221: message id chosen by the caller in message.ExtractSigner

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests choose a message/request id at the signed gateway message envelope submitted to the public user endpoint that collides with another user's in-flight request tracked via `ExtractSigner`, so responses are cross-delivered?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `ExtractSigner`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: the signature bytes (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `signature bytes` reusing a victim's id.
- Invariant to test: request identity must include the authenticated sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test issuing two requests with the same id from different senders
