# Q5418: state parameter not verified in session.SetAuthToken

## Question
Is the state/nonce checked by `SetAuthToken` at POST /sessions (session creation) and API-token authentication unbound to the initiating browser session, letting an unauthenticated HTTP client that can reach the node API port inject their own authorization code and take over the resulting node session?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `SetAuthToken`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: supplied access key and secret (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Deliver `supplied access key and secret` with an attacker-obtained code and a replayed state.
- Invariant to test: state must be single-use and bound to the initiating session
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test replaying state/code pairs across sessions
