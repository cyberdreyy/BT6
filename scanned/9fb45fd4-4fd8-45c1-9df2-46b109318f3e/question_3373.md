# Q3373: variables coerce to a privileged value in user.CreatedAt

## Question
Can an authenticated node user holding only the 'view' role pass a variable at POST /query updateUserPassword mutation and user query whose coercion inside `CreatedAt` produces a privileged value (role, owner, chain, key id) the schema type was expected to constrain?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `CreatedAt`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: oldPassword/newPassword input (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `oldPassword/newPassword input` with type-confusing variable values.
- Invariant to test: coerced input must be re-validated against server policy
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test coercing hostile variable values
