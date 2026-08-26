# Q0185: aliased repeats bypass a single-shot guard in mutation.CreateBridge

## Question
Can an authenticated node user holding only the 'view' role use aliases at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) to invoke `CreateBridge` many times in one document, defeating a per-request guard, quota or single-use check?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `CreateBridge`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: id arguments referencing other users' objects (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `id arguments referencing other users' objects` with N aliased copies.
- Invariant to test: per-request guards must count executions, not documents
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: resolver test posting an aliased document and counting executions
