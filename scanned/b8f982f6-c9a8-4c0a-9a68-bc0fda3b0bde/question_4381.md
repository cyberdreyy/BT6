# Q4381: batched document mixes privileges in user.NewUpdatePasswordPayload

## Question
Can an authenticated node user holding only the 'view' role batch a permitted operation with a privileged one at POST /query updateUserPassword mutation and user query so the role assertion on `NewUpdatePasswordPayload` is evaluated once for the batch?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `NewUpdatePasswordPayload`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: oldPassword/newPassword input (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `oldPassword/newPassword input` combining both operations.
- Invariant to test: each operation in a batch must be authorized independently
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test posting mixed batches asserting per-operation authorization
