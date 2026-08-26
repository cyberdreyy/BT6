# Q5088: aliased repeats bypass a single-shot guard in user.ToUpdatePasswordSuccess

## Question
Can an authenticated node user holding only the 'view' role use aliases at POST /query updateUserPassword mutation and user query to invoke `ToUpdatePasswordSuccess` many times in one document, defeating a per-request guard, quota or single-use check?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `ToUpdatePasswordSuccess`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: oldPassword/newPassword input (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `oldPassword/newPassword input` with N aliased copies.
- Invariant to test: per-request guards must count executions, not documents
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: resolver test posting an aliased document and counting executions
