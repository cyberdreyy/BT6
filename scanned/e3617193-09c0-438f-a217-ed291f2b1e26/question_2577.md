# Q2577: authorization key check bypassed in bundler.setSignedResponse

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests pass the authorization/allowlist check reached by `setSignedResponse` at bundling of node responses returned to the requesting gateway user with a missing, empty or differently-encoded key, obtaining unauthorized DON execution?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/bundler.go](core/services/gateway/handlers/confidentialrelay/bundler.go) -> `setSignedResponse`
- Entrypoint: bundling of node responses returned to the requesting gateway user
- Attacker controls: the request that determines bundle composition (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `request that determines bundle composition` with the key absent/empty/re-encoded.
- Invariant to test: authorization must fail closed and compare canonicalized values
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: table test over the authorization check with degenerate keys
