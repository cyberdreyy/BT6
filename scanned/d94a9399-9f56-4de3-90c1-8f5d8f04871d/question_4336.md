# Q4336: body size / content-length mismatch in wsserver.handleRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present a body whose declared and actual length differ at the public gateway WebSocket endpoint and its auth handshake so `handleRequest` validates a prefix and forwards the full payload to the DON?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `handleRequest`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: the challenge response signature (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `challenge response signature` with mismatched framing.
- Invariant to test: the bytes validated must be exactly the bytes forwarded
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test comparing validated bytes to forwarded bytes
