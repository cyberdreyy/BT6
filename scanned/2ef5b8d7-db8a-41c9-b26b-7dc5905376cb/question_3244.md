# Q3244: batched document mixes privileges in api_token.Secret

## Question
Can an authenticated node user holding only the 'view' role batch a permitted operation with a privileged one at POST /query createAPIToken/deleteAPIToken mutations so the role assertion on `Secret` is evaluated once for the batch?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `Secret`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: the returned token fields selected (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `returned token fields selected` combining both operations.
- Invariant to test: each operation in a batch must be authorized independently
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test posting mixed batches asserting per-operation authorization
