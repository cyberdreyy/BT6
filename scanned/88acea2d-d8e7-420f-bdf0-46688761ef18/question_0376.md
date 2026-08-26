# Q0376: message id chosen by the caller in codec.Codec

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests choose a message/request id at the encode/decode boundary for gateway user requests and responses that collides with another user's in-flight request tracked via `Codec`, so responses are cross-delivered?

## Target
- File/function: [core/services/gateway/api/codec.go](core/services/gateway/api/codec.go) -> `Codec`
- Entrypoint: the encode/decode boundary for gateway user requests and responses
- Attacker controls: response correlation fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `response correlation fields` reusing a victim's id.
- Invariant to test: request identity must include the authenticated sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test issuing two requests with the same id from different senders
