# Q3645: signature malleability accepted in wsserver.handleRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present a malleable or alternative-encoding signature at the public gateway WebSocket endpoint and its auth handshake that `handleRequest` accepts, producing a second valid form of an existing request (replay under a new id)?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `handleRequest`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: frames sent after upgrade (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `frames sent after upgrade` with high-S/alternate v/padded r-s values.
- Invariant to test: signature encoding must be canonical and single-valued
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over ExtractSigner with malleable signatures
