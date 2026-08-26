# Q0611: legacy and JSON-RPC envelopes disagree in message.Validate

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit the same logical request in the alternate envelope form at the signed gateway message envelope submitted to the public user endpoint so `Validate` applies weaker validation or a different identity?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `Validate`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: the signature bytes (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `signature bytes` in both envelope forms.
- Invariant to test: both envelope forms must converge on identical validation and identity
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test across the two envelope paths
