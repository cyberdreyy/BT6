# Q1628: timestamp/nonce window too wide in handshake.PackAuthHeader

## Question
Does the freshness check near `PackAuthHeader` at the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange) accept a wide or unbounded window, letting any internet client with an arbitrary externally-owned key sending signed gateway requests replay captured requests long after capture?

## Target
- File/function: [core/services/gateway/network/handshake.go](core/services/gateway/network/handshake.go) -> `PackAuthHeader`
- Entrypoint: the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange)
- Attacker controls: the signed auth header bytes (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `signed auth header bytes` with old/future timestamps.
- Invariant to test: freshness must be bounded and monotonic per sender
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test at the window boundaries
