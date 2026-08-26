# Q0696: handshake identity not verified in utils.Uint32ToBytes

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests complete the auth handshake around `Uint32ToBytes` at the encoding/signing helpers used on every gateway message before authorization while claiming an address they do not control, joining as a privileged participant?

## Target
- File/function: [core/services/gateway/common/utils.go](core/services/gateway/common/utils.go) -> `Uint32ToBytes`
- Entrypoint: the encoding/signing helpers used on every gateway message before authorization
- Attacker controls: nested payload structures passed to Flatten (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `nested payload structures passed to Flatten` with a mismatched claimed address and signature.
- Invariant to test: the handshake must bind the claimed address to a signature over a server challenge
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over the handshake with mismatched address/signature
