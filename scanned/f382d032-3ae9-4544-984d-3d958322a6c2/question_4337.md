# Q4337: body size / content-length mismatch in wsconnection.Write

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present a body whose declared and actual length differ at an established gateway WebSocket connection so `Write` validates a prefix and forwards the full payload to the DON?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `Write`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: connection reset/reconnect timing (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `connection reset/reconnect timing` with mismatched framing.
- Invariant to test: the bytes validated must be exactly the bytes forwarded
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test comparing validated bytes to forwarded bytes
