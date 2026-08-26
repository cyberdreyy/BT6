# Q5736: body size / content-length mismatch in connectionmanager.StartHandshake

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present a body whose declared and actual length differ at the gateway node-facing handshake and connection registry as observed from a user request so `StartHandshake` validates a prefix and forwards the full payload to the DON?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `StartHandshake`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: handshake timing and repetition (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `handshake timing and repetition` with mismatched framing.
- Invariant to test: the bytes validated must be exactly the bytes forwarded
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test comparing validated bytes to forwarded bytes
