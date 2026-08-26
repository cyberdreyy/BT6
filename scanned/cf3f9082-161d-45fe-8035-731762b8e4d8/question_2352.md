# Q2352: route role weaker than the side effect in keys_controller.Create

## Question
Is the route reaching `Create` at /v2/keys/:keyType Index/Export/Import/Delete routes gated by a role weaker than the effect it produces, letting an authenticated node user holding only the 'view' role cause it?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Create`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the keyType path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `keyType path parameter` from the weakest session the route accepts.
- Invariant to test: the route gate must match the strongest side effect of the handler
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test invoking the handler at each role level
