# Q1083: body size / content-length mismatch in message.Validate

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present a body whose declared and actual length differ at the signed gateway message envelope submitted to the public user endpoint so `Validate` validates a prefix and forwards the full payload to the DON?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `Validate`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: the signature bytes (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `signature bytes` with mismatched framing.
- Invariant to test: the bytes validated must be exactly the bytes forwarded
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test comparing validated bytes to forwarded bytes
