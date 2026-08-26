# Q4130: id argument not ownership-checked in api_token.NewCreateAPITokenPayload

## Question
Can an authenticated node user holding only the 'view' role pass an identifier for another user's object into `NewCreateAPITokenPayload` at POST /query createAPIToken/deleteAPIToken mutations and read or mutate it because only authentication, not ownership, is verified?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `NewCreateAPITokenPayload`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: aliased repeats of the mutation (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `aliased repeats of the mutation` with an id belonging to another owner.
- Invariant to test: object access must verify ownership/scope in addition to role
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test using another owner's id and asserting rejection
