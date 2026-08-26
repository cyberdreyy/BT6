# Q1079: body size / content-length mismatch in httpserver.ensureLimiters

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present a body whose declared and actual length differ at the public gateway user HTTP endpoint (POST to the configured user path) so `ensureLimiters` validates a prefix and forwards the full payload to the DON?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `ensureLimiters`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: request body bytes and Content-Length (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `request body bytes and Content-Length` with mismatched framing.
- Invariant to test: the bytes validated must be exactly the bytes forwarded
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test comparing validated bytes to forwarded bytes
