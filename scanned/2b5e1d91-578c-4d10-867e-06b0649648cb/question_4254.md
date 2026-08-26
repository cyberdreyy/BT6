# Q4254: session store keyed on user input in user_controller.Create

## Question
Is any session/MFA store keyed by a value an authenticated node user holding only the 'view' role supplies at /v2/users and /v2/user/* (password change, API token create/delete) on the path through `Create`, allowing collision with another user's entry?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Create`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: oldPassword/newPassword fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `oldPassword/newPassword fields` chosen to collide with an operator's key.
- Invariant to test: server-side session state must be keyed by an unguessable server-generated id
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting store keys are server-generated
