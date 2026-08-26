# Q2057: batched document mixes privileges in mutation.CreateCSAKey

## Question
Can an authenticated node user holding only the 'view' role batch a permitted operation with a privileged one at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) so the role assertion on `CreateCSAKey` is evaluated once for the batch?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `CreateCSAKey`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: id arguments referencing other users' objects (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `id arguments referencing other users' objects` combining both operations.
- Invariant to test: each operation in a batch must be authorized independently
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test posting mixed batches asserting per-operation authorization
