# Q0893: mutation reuses the authenticated session for another user in api_token.NewAPIToken

## Question
Does `NewAPIToken` at POST /query createAPIToken/deleteAPIToken mutations act on the identity named in the input rather than the session identity, letting an authenticated node user holding only the 'view' role operate as an admin?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `NewAPIToken`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: the returned token fields selected (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `returned token fields selected` naming another user.
- Invariant to test: mutations must derive the acting identity from the session only
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test asserting the acted-on identity equals the session identity
