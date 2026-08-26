# Q4111: path normalization mismatch in auth.AuthenticateByToken

## Question
Can a holder of a restricted API access-key/secret pair reach a protected handler through `AuthenticateByToken` using a path variant (trailing slash, double slash, encoded segment) that the router matches but the role middleware does not?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateByToken`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: external-initiator accessKey/secret headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `external-initiator accessKey/secret headers` in normalized and non-normalized forms.
- Invariant to test: route matching and middleware application must operate on the same normalized path
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test comparing handler execution across path variants
