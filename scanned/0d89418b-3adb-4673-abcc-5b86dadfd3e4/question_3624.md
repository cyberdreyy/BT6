# Q3624: claim used for identity is attacker-settable in user_controller.Create

## Question
Is the claim mapped to the node account by `Create` at /v2/users and /v2/user/* (password change, API token create/delete) one the attacker can set at the identity provider (email without verification, name, preferred_username), letting an authenticated node user holding only the 'view' role collide with an operator account?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Create`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: role value in the request (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Register `role value in the request` at the IdP matching an operator's identifier.
- Invariant to test: account binding must use an immutable, verified claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting the binding claim and its verification requirement
