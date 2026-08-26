# Q3459: empty or absent signature accepted in message.Sign

## Question
Does a request with an empty, zero or absent signature at the signed gateway message envelope submitted to the public user endpoint pass through `Sign` and receive an identity (zero address) that later checks treat as valid?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `Sign`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: field encoding and duplicate JSON keys (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `field encoding and duplicate JSON keys` without signature material.
- Invariant to test: missing signatures must be rejected before identity assignment
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test with empty/zero signatures
