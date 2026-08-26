# Q0375: message id chosen by the caller in jsonrpccodec.DecodeRawRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests choose a message/request id at the JSON-RPC request body accepted at the public gateway user endpoint that collides with another user's in-flight request tracked via `DecodeRawRequest`, so responses are cross-delivered?

## Target
- File/function: [core/services/gateway/api/jsonrpccodec.go](core/services/gateway/api/jsonrpccodec.go) -> `DecodeRawRequest`
- Entrypoint: the JSON-RPC request body accepted at the public gateway user endpoint
- Attacker controls: duplicate/unknown JSON fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `duplicate/unknown JSON fields` reusing a victim's id.
- Invariant to test: request identity must include the authenticated sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test issuing two requests with the same id from different senders
