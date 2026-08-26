# Q5658: delete/disable reachable below role in user.ToUpdatePasswordSuccess

## Question
Can an authenticated node user holding only the 'view' role disable or delete an object through `ToUpdatePasswordSuccess` at POST /query updateUserPassword mutation and user query (feeds manager, bridge, key, job) with only view/run rights, degrading oracle reporting?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `ToUpdatePasswordSuccess`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: oldPassword/newPassword input (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `oldPassword/newPassword input` from a low-role session.
- Invariant to test: destructive mutations require the admin role
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: resolver test invoking destructive mutations from low-role sessions
