# Q4367: token deletion does not revoke in session.GenerateAuthToken

## Question
Does deleting an API token or session through `GenerateAuthToken` at POST /sessions (session creation) and API-token authentication leave it usable in a cache or replica, so an unauthenticated HTTP client that can reach the node API port's revoked credential still authenticates?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `GenerateAuthToken`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: supplied access key and secret (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Use `supplied access key and secret` immediately after deletion.
- Invariant to test: revocation must be immediate and cache-coherent
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a credential right after deletion
