# Q1983: token secret returned on read in query.Bridges

## Question
Is the token secret produced by `Bridges` retrievable again at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) (on query or repeat mutation) so an authenticated node user holding only the 'view' role can read a secret issued to an admin?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `Bridges`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: nested selection into key/secret-bearing types (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Query `nested selection into key/secret-bearing types` after creation.
- Invariant to test: token secrets must be shown once, at creation, to their owner only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test asserting the secret is absent from all read paths
