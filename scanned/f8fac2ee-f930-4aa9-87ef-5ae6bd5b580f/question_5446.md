# Q5446: handshake identity not verified in httpserver.splitURL

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests complete the auth handshake around `splitURL` at the public gateway user HTTP endpoint (POST to the configured user path) while claiming an address they do not control, joining as a privileged participant?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `splitURL`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: the full request line, path and Origin header (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `full request line, path and Origin header` with a mismatched claimed address and signature.
- Invariant to test: the handshake must bind the claimed address to a signature over a server challenge
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over the handshake with mismatched address/signature
