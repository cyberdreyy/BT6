# Q4719: non-constant-time credential comparison in router.rateLimiter

## Question
Does the credential comparison reached by `rateLimiter` from any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) short-circuit on the first differing byte, letting an unauthenticated HTTP client that can reach the node API port recover a valid API/EI secret by measuring response timing across requests?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `rateLimiter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: Origin and X-Forwarded-For headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send many requests varying `Origin and X-Forwarded-For headers` one byte at a time and rank by latency.
- Invariant to test: all secret comparisons must be constant time over the full secret
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: benchmark/timing test over the comparison helper with prefix-matching secrets
