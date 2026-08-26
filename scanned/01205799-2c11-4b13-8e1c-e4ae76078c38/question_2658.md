# Q2658: token compared without constant time in orm.FindUser

## Question
Does the secret comparison used by `FindUser` at POST /sessions, API-token auth headers and session cookie lookup leak byte position through timing or early return, letting an unauthenticated HTTP client that can reach the node API port recover an admin API secret?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `FindUser`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: access key/secret pair (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send timed requests varying `access key/secret pair`.
- Invariant to test: all token/secret comparisons must be constant time
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: timing test over the comparison helper with prefix-matched secrets
