# Q2716: non-constant-time credential comparison in api.paginationLink

## Question
Does the credential comparison reached by `paginationLink` from page/size query parameters on /v2 index endpoints short-circuit on the first differing byte, letting an authenticated node user holding only the 'view' role recover a valid API/EI secret by measuring response timing across requests?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `paginationLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: page and size query values (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send many requests varying `page and size query values` one byte at a time and rank by latency.
- Invariant to test: all secret comparisons must be constant time over the full secret
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: benchmark/timing test over the comparison helper with prefix-matching secrets
