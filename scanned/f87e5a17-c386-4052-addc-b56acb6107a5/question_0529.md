# Q0529: JSON parsing differential in wsserver.NewWebSocketServer

## Question
Do duplicate keys, unknown fields or type coercion in the body parsed by `NewWebSocketServer` at the public gateway WebSocket endpoint and its auth handshake let any internet client with an arbitrary externally-owned key sending signed gateway requests present one value to validation and another to execution?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `NewWebSocketServer`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: frames sent after upgrade (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `frames sent after upgrade` with duplicate/aliased keys.
- Invariant to test: decoding must reject duplicates/unknown fields and be used once
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test decoding hostile JSON twice and comparing
