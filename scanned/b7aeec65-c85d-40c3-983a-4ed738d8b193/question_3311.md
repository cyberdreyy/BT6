# Q3311: introspection maps privileged surface in mutation.DeleteCSAKey

## Question
Can an authenticated node user holding only the 'view' role use introspection at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) to enumerate the mutations guarded near `DeleteCSAKey` and their inputs, then probe for the weakest one?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `DeleteCSAKey`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: the mutation name and input object (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Introspect with `mutation name and input object` and enumerate privileged fields.
- Invariant to test: if introspection is exposed, every field it reveals must still enforce its own role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting introspection-listed mutations all reject view-role sessions
