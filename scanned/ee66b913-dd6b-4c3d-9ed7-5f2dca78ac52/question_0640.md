# Q0640: rate limiter keyed on spoofable input in common.getChain

## Question
Can an authenticated node user holding only the 'view' role bypass the login/asset rate limiter reached by `getChain` by varying a client-controlled header used as the limiter key, enabling unbounded credential guessing against the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: chain id formatting (leading zeros, alternate base) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `chain id formatting (leading zeros, alternate base)` (X-Forwarded-For, session id) across requests.
- Invariant to test: the limiter key must be derived from server-observed connection identity
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: handler test sending N+1 requests with rotating forwarded-for headers asserting a 429
