# Q5871: token lookup ignores scope in user.ValidateAndHashPassword

## Question
Does the API token lookup performed by `ValidateAndHashPassword` at POST /sessions and PATCH /v2/user/password return a user without checking the token's owner, expiry or state, letting an unauthenticated HTTP client that can reach the node API port present a deleted user's token?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateAndHashPassword`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: password bytes and length (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `password bytes and length` belonging to a deleted or downgraded account.
- Invariant to test: token authentication must re-validate the owning account's existence and role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a token after its owner is deleted
