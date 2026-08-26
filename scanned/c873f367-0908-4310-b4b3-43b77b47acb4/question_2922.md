# Q2922: API token minted for another identity in user_controller.Create

## Question
Can an authenticated node user holding only the 'view' role cause `Create` at /v2/users and /v2/user/* (password change, API token create/delete) to mint or return an API token bound to a different (higher-role) user by controlling the identifier in the request?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Create`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: API token access key (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `API token access key` naming another user while authenticated as a low-role user.
- Invariant to test: tokens may only be issued for the authenticated identity
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the created token's user equals the session user
