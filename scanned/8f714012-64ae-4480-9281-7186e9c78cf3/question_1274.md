# Q1274: state parameter not verified in orm.NewORM

## Question
Is the state/nonce checked by `NewORM` at POST /sessions, API-token auth headers and session cookie lookup unbound to the initiating browser session, letting an unauthenticated HTTP client that can reach the node API port inject their own authorization code and take over the resulting node session?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `NewORM`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: email casing/whitespace (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Deliver `email casing/whitespace` with an attacker-obtained code and a replayed state.
- Invariant to test: state must be single-use and bound to the initiating session
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test replaying state/code pairs across sessions
