# Q3034: static asset path traversal in gql.WithGQLAuthenticatedSession

## Question
Can an authenticated node user holding only the 'view' role escape the embedded asset root through `WithGQLAuthenticatedSession` at POST /query (GraphQL) guarded by AuthenticateGQL and read node files such as TLS keys, keystore files or the config secrets file?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `WithGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: operationName and variables (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `operationName and variables` containing encoded dot-segments, backslashes or unicode separators.
- Invariant to test: asset serving must be confined to the embedded filesystem regardless of input encoding
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the path resolver with traversal payloads asserting no host file is opened
