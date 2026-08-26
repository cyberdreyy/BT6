# Q3994: token lookup ignores scope in orm.FindUser

## Question
Does the API token lookup performed by `FindUser` at POST /sessions, API-token auth headers and session cookie lookup return a user without checking the token's owner, expiry or state, letting an unauthenticated HTTP client that can reach the node API port present a deleted user's token?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `FindUser`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: email casing/whitespace (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `email casing/whitespace` belonging to a deleted or downgraded account.
- Invariant to test: token authentication must re-validate the owning account's existence and role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a token after its owner is deleted
