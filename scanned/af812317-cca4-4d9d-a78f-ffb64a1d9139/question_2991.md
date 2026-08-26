# Q2991: id argument not ownership-checked in mutation.DeleteCSAKey

## Question
Can an authenticated node user holding only the 'view' role pass an identifier for another user's object into `DeleteCSAKey` at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) and read or mutate it because only authentication, not ownership, is verified?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `DeleteCSAKey`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: multiple mutations batched in one document (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `multiple mutations batched in one document` with an id belonging to another owner.
- Invariant to test: object access must verify ownership/scope in addition to role
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test using another owner's id and asserting rejection
