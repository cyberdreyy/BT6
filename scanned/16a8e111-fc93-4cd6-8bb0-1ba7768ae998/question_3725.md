# Q3725: per-owner quota not enforced in bundler.setSignedResponse

## Question
Does `setSignedResponse` at bundling of node responses returned to the requesting gateway user enforce rate/quota per authenticated owner, or can any internet client with an arbitrary externally-owned key sending signed gateway requests rotate identifiers to obtain unlimited DON execution charged elsewhere?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/bundler.go](core/services/gateway/handlers/confidentialrelay/bundler.go) -> `setSignedResponse`
- Entrypoint: bundling of node responses returned to the requesting gateway user
- Attacker controls: the request that determines bundle composition (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `request that determines bundle composition` across submissions.
- Invariant to test: quotas must key on the verified owner and be enforced before dispatch
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test rotating identifiers and asserting the quota still applies
