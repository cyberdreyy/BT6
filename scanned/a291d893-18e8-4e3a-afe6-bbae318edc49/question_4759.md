# Q4759: timestamp/nonce window too wide in message.SignKS

## Question
Does the freshness check near `SignKS` at the signed gateway message envelope submitted to the public user endpoint accept a wide or unbounded window, letting any internet client with an arbitrary externally-owned key sending signed gateway requests replay captured requests long after capture?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `SignKS`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: field encoding and duplicate JSON keys (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `field encoding and duplicate JSON keys` with old/future timestamps.
- Invariant to test: freshness must be bounded and monotonic per sender
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test at the window boundaries
