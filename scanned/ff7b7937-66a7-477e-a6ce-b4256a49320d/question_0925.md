# Q0925: origin allowlist bypass in wsconnection.NewWSConnectionWrapper

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests bypass the origin check in `NewWSConnectionWrapper` at an established gateway WebSocket connection with case, suffix, port or null-origin tricks and drive the gateway from a browser context?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `NewWSConnectionWrapper`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: concurrent connections claiming the same identity (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `concurrent connections claiming the same identity` with crafted Origin values.
- Invariant to test: origin matching must be exact against the configured list
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over isAllowedOrigin with hostile origins
