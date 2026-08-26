# Q3225: pagination parameter injection in auth.AuthenticateByToken

## Question
Can a holder of a restricted API access-key/secret pair pass a crafted page/size value through `AuthenticateByToken` on any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list that reaches the query layer unvalidated and returns rows belonging to other users or unfiltered secret columns?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateByToken`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: X-API-KEY and X-API-SECRET headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `X-API-KEY and X-API-SECRET headers` with negative, overflowing or non-numeric values.
- Invariant to test: pagination inputs must be validated and never widen the row filter
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over ParsePaginatedRequest with hostile values asserting bounded output
