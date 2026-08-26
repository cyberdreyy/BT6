# Q5645: session cookie attributes in authentication.AuthenticationProvider

## Question
Are the cookie attributes set around `AuthenticationProvider` at POST /sessions and every AuthenticationProvider call behind /v2 auth weak enough (missing Secure/HttpOnly/SameSite, overly broad Path or Domain) that an unauthenticated HTTP client that can reach the node API port can obtain or ride an operator session and then export keys?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `AuthenticationProvider`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: submitted email and password (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Observe the Set-Cookie produced for `submitted email and password` and exercise the weakest attribute.
- Invariant to test: session cookies must be HttpOnly, Secure and SameSite-restricted
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the Set-Cookie attribute set
