# Q5187: pagination parameter injection in api.nextLink

## Question
Can an authenticated node user holding only the 'view' role pass a crafted page/size value through `nextLink` on page/size query parameters on /v2 index endpoints that reaches the query layer unvalidated and returns rows belonging to other users or unfiltered secret columns?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `nextLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: Link header follow-up requests (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `Link header follow-up requests` with negative, overflowing or non-numeric values.
- Invariant to test: pagination inputs must be validated and never widen the row filter
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over ParsePaginatedRequest with hostile values asserting bounded output
