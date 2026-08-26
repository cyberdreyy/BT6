# Q0953: pagination parameter injection in helpers.jsonAPIError

## Question
Can an unauthenticated HTTP client that can reach the node API port pass a crafted page/size value through `jsonAPIError` on any /v2 or /query error response path that reaches the query layer unvalidated and returns rows belonging to other users or unfiltered secret columns?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `jsonAPIError`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: inputs that force an error branch (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `inputs that force an error branch` with negative, overflowing or non-numeric values.
- Invariant to test: pagination inputs must be validated and never widen the row filter
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over ParsePaginatedRequest with hostile values asserting bounded output
