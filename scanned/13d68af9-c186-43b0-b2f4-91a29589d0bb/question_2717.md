# Q2717: non-constant-time credential comparison in helpers.paginatedResponse

## Question
Does the credential comparison reached by `paginatedResponse` from the JSON:API response writer used by every /v2 controller short-circuit on the first differing byte, letting an authenticated node user holding only the 'view' role recover a valid API/EI secret by measuring response timing across requests?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedResponse`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: inputs that select the error branch (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send many requests varying `inputs that select the error branch` one byte at a time and rank by latency.
- Invariant to test: all secret comparisons must be constant time over the full secret
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: benchmark/timing test over the comparison helper with prefix-matching secrets
