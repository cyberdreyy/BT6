# Q2688: origin allowlist bypass in wsserver.handleHealthCheck

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests bypass the origin check in `handleHealthCheck` at the public gateway WebSocket endpoint and its auth handshake with case, suffix, port or null-origin tricks and drive the gateway from a browser context?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `handleHealthCheck`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: claimed node/user address (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `claimed node/user address` with crafted Origin values.
- Invariant to test: origin matching must be exact against the configured list
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over isAllowedOrigin with hostile origins
