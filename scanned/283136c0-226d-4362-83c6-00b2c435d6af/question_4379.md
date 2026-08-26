# Q4379: batched document mixes privileges in auth.authenticateUserIsAdmin

## Question
Can an authenticated node user holding only the 'view' role batch a permitted operation with a privileged one at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin so the role assertion on `authenticateUserIsAdmin` is evaluated once for the batch?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUserIsAdmin`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: variables (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `variables` combining both operations.
- Invariant to test: each operation in a batch must be authorized independently
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test posting mixed batches asserting per-operation authorization
