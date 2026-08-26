# Q3746: session cookie attributes in sync.Work

## Question
Are the cookie attributes set around `Work` at any authenticated /v2 request after LDAP group membership is revoked weak enough (missing Secure/HttpOnly/SameSite, overly broad Path or Domain) that an authenticated node user holding only the 'view' role can obtain or ride an operator session and then export keys?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `Work`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: timing between group revocation and the sync tick (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Observe the Set-Cookie produced for `timing between group revocation and the sync tick` and exercise the weakest attribute.
- Invariant to test: session cookies must be HttpOnly, Secure and SameSite-restricted
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the Set-Cookie attribute set
