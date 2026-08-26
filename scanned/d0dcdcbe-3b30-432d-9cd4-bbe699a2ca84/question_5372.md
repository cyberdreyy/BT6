# Q5372: token secret returned on read in api_token.ToCreateAPITokenSuccess

## Question
Is the token secret produced by `ToCreateAPITokenSuccess` retrievable again at POST /query createAPIToken/deleteAPIToken mutations (on query or repeat mutation) so an authenticated node user holding only the 'view' role can read a secret issued to an admin?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `ToCreateAPITokenSuccess`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: aliased repeats of the mutation (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Query `aliased repeats of the mutation` after creation.
- Invariant to test: token secrets must be shown once, at creation, to their owner only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test asserting the secret is absent from all read paths
