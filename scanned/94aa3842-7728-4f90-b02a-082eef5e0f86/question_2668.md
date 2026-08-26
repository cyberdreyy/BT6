# Q2668: resolver executes before auth on error in api_token.AccessKey

## Question
Does `AccessKey` at POST /query createAPIToken/deleteAPIToken mutations perform its side effect before its role assertion returns, so an authenticated node user holding only the 'view' role still causes the change while receiving an authorization error?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `AccessKey`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: the returned token fields selected (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `returned token fields selected` and inspect state afterwards.
- Invariant to test: authorization must complete before any side effect
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test asserting no state change accompanies an authorization error
