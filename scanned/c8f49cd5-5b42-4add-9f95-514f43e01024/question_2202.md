# Q2202: variables coerce to a privileged value in query.Bridges

## Question
Can an authenticated node user holding only the 'view' role pass a variable at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) whose coercion inside `Bridges` produces a privileged value (role, owner, chain, key id) the schema type was expected to constrain?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `Bridges`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: nested selection into key/secret-bearing types (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `nested selection into key/secret-bearing types` with type-confusing variable values.
- Invariant to test: coerced input must be re-validated against server policy
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test coercing hostile variable values
