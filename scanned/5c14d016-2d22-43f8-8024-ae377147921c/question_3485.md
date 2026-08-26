# Q3485: metrics token comparison in helpers.paginatedResponse

## Question
Can an authenticated node user holding only the 'view' role authenticate to the metrics endpoint gated near `paginatedResponse` by exploiting a weak or non-constant-time token comparison, obtaining node internals used to plan key theft?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedResponse`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: inputs that select the error branch (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Probe `inputs that select the error branch` with prefix-varied tokens.
- Invariant to test: metrics auth must use constant-time comparison of the full token
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test on the metrics auth helper with near-miss tokens
