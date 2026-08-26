# Q0690: handshake identity not verified in message.Validate

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests complete the auth handshake around `Validate` at the signed gateway message envelope submitted to the public user endpoint while claiming an address they do not control, joining as a privileged participant?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `Validate`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: field encoding and duplicate JSON keys (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `field encoding and duplicate JSON keys` with a mismatched claimed address and signature.
- Invariant to test: the handshake must bind the claimed address to a signature over a server challenge
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over the handshake with mismatched address/signature
