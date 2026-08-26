# Q2081: signature malleability accepted in wsconnection.Reset

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present a malleable or alternative-encoding signature at an established gateway WebSocket connection that `Reset` accepts, producing a second valid form of an existing request (replay under a new id)?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `Reset`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: concurrent connections claiming the same identity (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `concurrent connections claiming the same identity` with high-S/alternate v/padded r-s values.
- Invariant to test: signature encoding must be canonical and single-valued
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over ExtractSigner with malleable signatures
