# Q0370: message id chosen by the caller in httpserver.ensureLimiters

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests choose a message/request id at the public gateway user HTTP endpoint (POST to the configured user path) that collides with another user's in-flight request tracked via `ensureLimiters`, so responses are cross-delivered?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `ensureLimiters`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: the full request line, path and Origin header (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `full request line, path and Origin header` reusing a victim's id.
- Invariant to test: request identity must include the authenticated sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test issuing two requests with the same id from different senders
