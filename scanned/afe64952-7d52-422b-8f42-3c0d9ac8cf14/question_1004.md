# Q1004: path/URL split confusion in handshake.PackAuthHeader

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a request path at the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange) that `PackAuthHeader` splits differently from the routing layer, reaching a handler or DON that was not authorized?

## Target
- File/function: [core/services/gateway/network/handshake.go](core/services/gateway/network/handshake.go) -> `PackAuthHeader`
- Entrypoint: the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange)
- Attacker controls: the signed auth header bytes (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `signed auth header bytes` with extra segments, encoded slashes or empty segments.
- Invariant to test: splitting and routing must agree on the same canonical path
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over splitURL with hostile paths
