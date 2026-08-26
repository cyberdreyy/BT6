# Q1753: id argument not ownership-checked in user.Email

## Question
Can an authenticated node user holding only the 'view' role pass an identifier for another user's object into `Email` at POST /query updateUserPassword mutation and user query and read or mutate it because only authentication, not ownership, is verified?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `Email`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: oldPassword/newPassword input (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `oldPassword/newPassword input` with an id belonging to another owner.
- Invariant to test: object access must verify ownership/scope in addition to role
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test using another owner's id and asserting rejection
