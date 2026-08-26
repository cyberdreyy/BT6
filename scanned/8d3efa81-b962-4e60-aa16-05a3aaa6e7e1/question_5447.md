# Q5447: handshake identity not verified in wsconnection.ReadChannel

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests complete the auth handshake around `ReadChannel` at an established gateway WebSocket connection while claiming an address they do not control, joining as a privileged participant?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `ReadChannel`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: concurrent connections claiming the same identity (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `concurrent connections claiming the same identity` with a mismatched claimed address and signature.
- Invariant to test: the handshake must bind the claimed address to a signature over a server challenge
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over the handshake with mismatched address/signature
