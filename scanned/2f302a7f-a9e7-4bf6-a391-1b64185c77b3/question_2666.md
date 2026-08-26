# Q2666: token compared without constant time in user_controller.Create

## Question
Does the secret comparison used by `Create` at /v2/users and /v2/user/* (password change, API token create/delete) leak byte position through timing or early return, letting an authenticated node user holding only the 'view' role recover an admin API secret?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Create`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: API token access key (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send timed requests varying `API token access key`.
- Invariant to test: all token/secret comparisons must be constant time
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: timing test over the comparison helper with prefix-matched secrets
