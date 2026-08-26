# Q3309: introspection maps privileged surface in user.CreatedAt

## Question
Can an authenticated node user holding only the 'view' role use introspection at POST /query updateUserPassword mutation and user query to enumerate the mutations guarded near `CreatedAt` and their inputs, then probe for the weakest one?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `CreatedAt`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: selection set on the User type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Introspect with `selection set on the User type` and enumerate privileged fields.
- Invariant to test: if introspection is exposed, every field it reveals must still enforce its own role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting introspection-listed mutations all reject view-role sessions
