# Q0847: connection identity rebinding in handshake.PackAuthHeader

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests open a connection through `PackAuthHeader` at the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange) and then act under an identity established by another connection because the registry is keyed loosely?

## Target
- File/function: [core/services/gateway/network/handshake.go](core/services/gateway/network/handshake.go) -> `PackAuthHeader`
- Entrypoint: the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange)
- Attacker controls: the signature and recovered address (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Open concurrent connections with `signature and recovered address` claiming the same identity.
- Invariant to test: each connection must carry its own verified identity for its lifetime
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: concurrency test asserting per-connection identity isolation
