# Q4239: double decoding of identifiers in helpers.addForbiddenErrorHeaders

## Question
Is an identifier decoded twice between the authorization check and the lookup on the path through `addForbiddenErrorHeaders`, letting an unauthenticated HTTP client that can reach the node API port authorize one object at any /v2 or /query error response path and act on another?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `addForbiddenErrorHeaders`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: inputs that force an error branch (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `inputs that force an error branch` percent-encoded so the two stages resolve to different values.
- Invariant to test: the value authorized and the value used must be byte-identical
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the authorized identifier equals the identifier passed to the store
