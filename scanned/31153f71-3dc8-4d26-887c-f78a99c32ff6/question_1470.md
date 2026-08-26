# Q1470: unauthenticated method reachable in wsserver.NewWebSocketServer

## Question
Is a gateway method routed by `NewWebSocketServer` at the public gateway WebSocket endpoint and its auth handshake reachable without the authorization the handler assumes, letting any internet client with an arbitrary externally-owned key sending signed gateway requests invoke privileged capability paths?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `NewWebSocketServer`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: frames sent after upgrade (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `frames sent after upgrade` for each advertised method without credentials.
- Invariant to test: every method must declare and enforce its own authorization
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: matrix test invoking every method unauthenticated
