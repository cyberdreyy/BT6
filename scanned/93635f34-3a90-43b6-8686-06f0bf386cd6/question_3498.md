# Q3498: state parameter not verified in user_controller.Create

## Question
Is the state/nonce checked by `Create` at /v2/users and /v2/user/* (password change, API token create/delete) unbound to the initiating browser session, letting an authenticated node user holding only the 'view' role inject their own authorization code and take over the resulting node session?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Create`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: oldPassword/newPassword fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Deliver `oldPassword/newPassword fields` with an attacker-obtained code and a replayed state.
- Invariant to test: state must be single-use and bound to the initiating session
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test replaying state/code pairs across sessions
