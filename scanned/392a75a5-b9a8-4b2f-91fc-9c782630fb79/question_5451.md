# Q5451: handshake identity not verified in gateway.ProcessRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests complete the auth handshake around `ProcessRequest` at ProcessRequest on the public gateway user endpoint while claiming an address they do not control, joining as a privileged participant?

## Target
- File/function: [core/services/gateway/gateway.go](core/services/gateway/gateway.go) -> `ProcessRequest`
- Entrypoint: ProcessRequest on the public gateway user endpoint
- Attacker controls: request repetition and concurrency (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `request repetition and concurrency` with a mismatched claimed address and signature.
- Invariant to test: the handshake must bind the claimed address to a signature over a server challenge
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over the handshake with mismatched address/signature
