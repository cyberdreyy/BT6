# Q3750: session cookie attributes in user_controller.Create

## Question
Are the cookie attributes set around `Create` at /v2/users and /v2/user/* (password change, API token create/delete) weak enough (missing Secure/HttpOnly/SameSite, overly broad Path or Domain) that an authenticated node user holding only the 'view' role can obtain or ride an operator session and then export keys?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Create`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: oldPassword/newPassword fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Observe the Set-Cookie produced for `oldPassword/newPassword fields` and exercise the weakest attribute.
- Invariant to test: session cookies must be HttpOnly, Secure and SameSite-restricted
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the Set-Cookie attribute set
