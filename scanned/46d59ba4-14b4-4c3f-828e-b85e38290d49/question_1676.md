# Q1676: nested selection reaches privileged sibling in query.Bridges

## Question
Can an authenticated node user holding only the 'view' role reach a privileged type from an unprivileged root through nested selections resolved by `Bridges` at POST /query read resolvers (bridges, jobs, keys, config, nodes, features), since only the root field carries the role check?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `Bridges`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: the queried field and arguments (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Traverse `queried field and arguments` into the privileged child type.
- Invariant to test: authorization must be enforced on each resolver, not only on root fields
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test traversing from an allowed root into privileged children
