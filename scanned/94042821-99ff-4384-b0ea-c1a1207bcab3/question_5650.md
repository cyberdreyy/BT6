# Q5650: session cookie attributes in reaper.deleteStaleSessions

## Question
Are the cookie attributes set around `deleteStaleSessions` at any authenticated /v2 request made after logout, password change or role change weak enough (missing Secure/HttpOnly/SameSite, overly broad Path or Domain) that an authenticated node user holding only the 'view' role can obtain or ride an operator session and then export keys?

## Target
- File/function: [core/sessions/localauth/reaper.go](core/sessions/localauth/reaper.go) -> `deleteStaleSessions`
- Entrypoint: any authenticated /v2 request made after logout, password change or role change
- Attacker controls: timing of requests relative to session/token lifetime (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Observe the Set-Cookie produced for `timing of requests relative to session/token lifetime` and exercise the weakest attribute.
- Invariant to test: session cookies must be HttpOnly, Secure and SameSite-restricted
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the Set-Cookie attribute set
