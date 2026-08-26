# Q3434: revocation not honoured until sync in user_controller.Create

## Question
Does the session/token created before revocation stay valid on the path through `Create` at /v2/users and /v2/user/* (password change, API token create/delete) until a background sync runs, giving an authenticated node user holding only the 'view' role a usable window with revoked privileges?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Create`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: API token access key (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Keep using `API token access key` across the revocation event.
- Invariant to test: revocation must take effect on the next request, not on the next sync tick
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test revoking access and asserting immediate rejection
