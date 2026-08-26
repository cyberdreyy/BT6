# Q4240: double decoding of identifiers in api.paginationLink

## Question
Is an identifier decoded twice between the authorization check and the lookup on the path through `paginationLink`, letting an authenticated node user holding only the 'view' role authorize one object at page/size query parameters on /v2 index endpoints and act on another?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `paginationLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: page and size query values (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `page and size query values` percent-encoded so the two stages resolve to different values.
- Invariant to test: the value authorized and the value used must be byte-identical
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the authorized identifier equals the identifier passed to the store
