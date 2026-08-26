# Q5486: introspection maps privileged surface in api_token.ToCreateAPITokenSuccess

## Question
Can an authenticated node user holding only the 'view' role use introspection at POST /query createAPIToken/deleteAPIToken mutations to enumerate the mutations guarded near `ToCreateAPITokenSuccess` and their inputs, then probe for the weakest one?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `ToCreateAPITokenSuccess`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: the password field in the mutation input (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Introspect with `password field in the mutation input` and enumerate privileged fields.
- Invariant to test: if introspection is exposed, every field it reveals must still enforce its own role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting introspection-listed mutations all reject view-role sessions
