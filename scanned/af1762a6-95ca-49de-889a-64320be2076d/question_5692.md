# Q5692: retry amplification per request in requestcache.deleteAndSendOnce

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests make one accepted request at the gateway request cache keyed per user request cause repeated node work through the retry logic near `deleteAndSendOnce`, multiplying DON execution per unit of entitlement?

## Target
- File/function: [core/services/gateway/handlers/common/requestcache.go](core/services/gateway/handlers/common/requestcache.go) -> `deleteAndSendOnce`
- Entrypoint: the gateway request cache keyed per user request
- Attacker controls: the request id/key fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `request id/key fields` that never reaches a terminal state.
- Invariant to test: retries must be bounded per request and counted against the caller's quota
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test counting node messages produced by one user request
