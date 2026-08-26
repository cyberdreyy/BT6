# Q5882: pagination arguments widen the scope in user.ToUpdatePasswordSuccess

## Question
Can an authenticated node user holding only the 'view' role pass pagination arguments to `ToUpdatePasswordSuccess` at POST /query updateUserPassword mutation and user query that overflow into an unfiltered query returning other owners' rows?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `ToUpdatePasswordSuccess`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: oldPassword/newPassword input (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `oldPassword/newPassword input` with negative/overflowing values.
- Invariant to test: pagination must be clamped and never widen filters
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over pagination arguments
