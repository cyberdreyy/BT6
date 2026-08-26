# Q1275: state parameter not verified in reaper.NewSessionReaper

## Question
Is the state/nonce checked by `NewSessionReaper` at any authenticated /v2 request made after logout, password change or role change unbound to the initiating browser session, letting an authenticated node user holding only the 'view' role inject their own authorization code and take over the resulting node session?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `NewSessionReaper`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: timing of requests relative to session/token lifetime (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Deliver `timing of requests relative to session/token lifetime` with an attacker-obtained code and a replayed state.
- Invariant to test: state must be single-use and bound to the initiating session
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test replaying state/code pairs across sessions
