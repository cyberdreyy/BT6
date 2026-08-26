# Q0264: nested selection reaches privileged sibling in mutation.CreateBridge

## Question
Can an authenticated node user holding only the 'view' role reach a privileged type from an unprivileged root through nested selections resolved by `CreateBridge` at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains), since only the root field carries the role check?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `CreateBridge`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: the mutation name and input object (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Traverse `mutation name and input object` into the privileged child type.
- Invariant to test: authorization must be enforced on each resolver, not only on root fields
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test traversing from an allowed root into privileged children
