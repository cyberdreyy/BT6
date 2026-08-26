# Q3264: timestamp/nonce window too wide in wsserver.handleHealthCheck

## Question
Does the freshness check near `handleHealthCheck` at the public gateway WebSocket endpoint and its auth handshake accept a wide or unbounded window, letting any internet client with an arbitrary externally-owned key sending signed gateway requests replay captured requests long after capture?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `handleHealthCheck`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: the auth header presented at handshake (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `auth header presented at handshake` with old/future timestamps.
- Invariant to test: freshness must be bounded and monotonic per sender
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test at the window boundaries
