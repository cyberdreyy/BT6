# Q2883: per-sender limits keyed on spoofable identity in message.Sign

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests evade the per-sender limiter reached by `Sign` at the signed gateway message envelope submitted to the public user endpoint by rotating an unauthenticated key (address field, IP header, connection), obtaining DON execution beyond entitlement?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `Sign`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: field encoding and duplicate JSON keys (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `field encoding and duplicate JSON keys` across requests.
- Invariant to test: limits must key on the cryptographically verified sender
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test rotating the limiter key and asserting throttling still applies
