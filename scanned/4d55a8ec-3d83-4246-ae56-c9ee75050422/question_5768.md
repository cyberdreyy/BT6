# Q5768: user enumeration then targeted attack in user_controller.UpdateRole

## Question
Do responses from `UpdateRole` at /v2/users and /v2/user/* (password change, API token create/delete) distinguish unknown accounts from wrong passwords precisely enough for an authenticated node user holding only the 'view' role to enumerate operator accounts before credential attacks?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `UpdateRole`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: role value in the request (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare status/body/timing for `role value in the request` across known and unknown accounts.
- Invariant to test: authentication failures must be uniform in content and timing
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test comparing responses for known/unknown accounts
