# Q0321: non-constant-time credential comparison in helpers.jsonAPIError

## Question
Does the credential comparison reached by `jsonAPIError` from any /v2 or /query error response path short-circuit on the first differing byte, letting an unauthenticated HTTP client that can reach the node API port recover a valid API/EI secret by measuring response timing across requests?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `jsonAPIError`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: malformed JSON bodies (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send many requests varying `malformed JSON bodies` one byte at a time and rank by latency.
- Invariant to test: all secret comparisons must be constant time over the full secret
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: benchmark/timing test over the comparison helper with prefix-matching secrets
