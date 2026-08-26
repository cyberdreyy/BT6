# Q4068: nested selection reaches privileged sibling in user.NewUpdatePasswordPayload

## Question
Can an authenticated node user holding only the 'view' role reach a privileged type from an unprivileged root through nested selections resolved by `NewUpdatePasswordPayload` at POST /query updateUserPassword mutation and user query, since only the root field carries the role check?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `NewUpdatePasswordPayload`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: selection set on the User type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Traverse `selection set on the User type` into the privileged child type.
- Invariant to test: authorization must be enforced on each resolver, not only on root fields
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test traversing from an allowed root into privileged children
