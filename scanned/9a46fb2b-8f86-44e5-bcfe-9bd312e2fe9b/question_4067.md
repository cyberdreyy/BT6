# Q4067: nested selection reaches privileged sibling in api_token.NewCreateAPITokenPayload

## Question
Can an authenticated node user holding only the 'view' role reach a privileged type from an unprivileged root through nested selections resolved by `NewCreateAPITokenPayload` at POST /query createAPIToken/deleteAPIToken mutations, since only the root field carries the role check?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `NewCreateAPITokenPayload`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: the password field in the mutation input (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Traverse `password field in the mutation input` into the privileged child type.
- Invariant to test: authorization must be enforced on each resolver, not only on root fields
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test traversing from an allowed root into privileged children
