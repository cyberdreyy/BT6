# Q3648: signature malleability accepted in message.SignKS

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present a malleable or alternative-encoding signature at the signed gateway message envelope submitted to the public user endpoint that `SignKS` accepts, producing a second valid form of an existing request (replay under a new id)?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `SignKS`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: field encoding and duplicate JSON keys (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `field encoding and duplicate JSON keys` with high-S/alternate v/padded r-s values.
- Invariant to test: signature encoding must be canonical and single-valued
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over ExtractSigner with malleable signatures
