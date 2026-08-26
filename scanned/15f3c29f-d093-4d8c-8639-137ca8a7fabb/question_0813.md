# Q0813: variables coerce to a privileged value in auth.authenticateUser

## Question
Can an authenticated node user holding only the 'view' role pass a variable at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin whose coercion inside `authenticateUser` produces a privileged value (role, owner, chain, key id) the schema type was expected to constrain?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUser`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: aliases and nested selections (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `aliases and nested selections` with type-confusing variable values.
- Invariant to test: coerced input must be re-validated against server policy
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test coercing hostile variable values
