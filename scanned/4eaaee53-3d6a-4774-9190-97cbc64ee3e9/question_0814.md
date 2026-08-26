# Q0814: variables coerce to a privileged value in api_token.NewAPIToken

## Question
Can an authenticated node user holding only the 'view' role pass a variable at POST /query createAPIToken/deleteAPIToken mutations whose coercion inside `NewAPIToken` produces a privileged value (role, owner, chain, key id) the schema type was expected to constrain?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `NewAPIToken`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: aliased repeats of the mutation (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `aliased repeats of the mutation` with type-confusing variable values.
- Invariant to test: coerced input must be re-validated against server policy
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test coercing hostile variable values
