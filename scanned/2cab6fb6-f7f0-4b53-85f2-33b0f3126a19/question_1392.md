# Q1392: validation happens after dispatch in wsserver.NewWebSocketServer

## Question
Does `NewWebSocketServer` at the public gateway WebSocket endpoint and its auth handshake dispatch the request to the DON before validation completes, so any internet client with an arbitrary externally-owned key sending signed gateway requests's invalid request still consumes DON work or reaches capability code?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `NewWebSocketServer`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: the challenge response signature (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `challenge response signature` that fails late in validation.
- Invariant to test: no dispatch may precede complete validation
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test asserting no node message is sent for invalid requests
