# Q0083: nil user with nil error in gql.AuthenticateGQL

## Question
Can an authenticated node user holding only the 'view' role reach a branch of `AuthenticateGQL` on POST /query (GraphQL) guarded by AuthenticateGQL where the authenticator returns a zero-valued user together with a nil error, so the request continues with an empty identity that later role checks treat as satisfied?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `AuthenticateGQL`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: operationName and variables (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `operationName and variables` to hit an early-return path that forgets to set the error before returning.
- Invariant to test: no code path may return a usable session/user value alongside a nil error
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting every error branch of the authenticator returns a non-nil error and a zero user
