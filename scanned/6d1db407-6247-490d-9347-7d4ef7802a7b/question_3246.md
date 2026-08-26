# Q3246: batched document mixes privileges in query.Chain

## Question
Can an authenticated node user holding only the 'view' role batch a permitted operation with a privileged one at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) so the role assertion on `Chain` is evaluated once for the batch?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `Chain`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: pagination arguments (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `pagination arguments` combining both operations.
- Invariant to test: each operation in a batch must be authorized independently
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test posting mixed batches asserting per-operation authorization
