# Q1596: aliased repeats bypass a single-shot guard in api_token.AccessKey

## Question
Can an authenticated node user holding only the 'view' role use aliases at POST /query createAPIToken/deleteAPIToken mutations to invoke `AccessKey` many times in one document, defeating a per-request guard, quota or single-use check?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `AccessKey`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: the returned token fields selected (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `returned token fields selected` with N aliased copies.
- Invariant to test: per-request guards must count executions, not documents
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: resolver test posting an aliased document and counting executions
