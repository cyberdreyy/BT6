# Q3960: legacy and JSON-RPC envelopes disagree in wsserver.handleRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit the same logical request in the alternate envelope form at the public gateway WebSocket endpoint and its auth handshake so `handleRequest` applies weaker validation or a different identity?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `handleRequest`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: claimed node/user address (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `claimed node/user address` in both envelope forms.
- Invariant to test: both envelope forms must converge on identical validation and identity
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test across the two envelope paths
