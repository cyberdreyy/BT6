# Q1754: id argument not ownership-checked in query.Bridges

## Question
Can an authenticated node user holding only the 'view' role pass an identifier for another user's object into `Bridges` at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) and read or mutate it because only authentication, not ownership, is verified?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `Bridges`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: nested selection into key/secret-bearing types (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `nested selection into key/secret-bearing types` with an id belonging to another owner.
- Invariant to test: object access must verify ownership/scope in addition to role
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test using another owner's id and asserting rejection
