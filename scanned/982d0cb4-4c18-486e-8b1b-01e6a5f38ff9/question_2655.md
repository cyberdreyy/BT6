# Q2655: token compared without constant time in session.GenerateAuthToken

## Question
Does the secret comparison used by `GenerateAuthToken` at POST /sessions (session creation) and API-token authentication leak byte position through timing or early return, letting an unauthenticated HTTP client that can reach the node API port recover an admin API secret?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `GenerateAuthToken`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: supplied access key and secret (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send timed requests varying `supplied access key and secret`.
- Invariant to test: all token/secret comparisons must be constant time
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: timing test over the comparison helper with prefix-matched secrets
