# Q2351: route role weaker than the side effect in bridge_types_controller.ValidateBridgeType

## Question
Is the route reaching `ValidateBridgeType` at POST/PATCH/GET /v2/bridge_types gated by a role weaker than the effect it produces, letting an authenticated node user holding only the 'edit' role (non-admin) cause it?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `ValidateBridgeType`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: bridge name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `bridge name and URL` from the weakest session the route accepts.
- Invariant to test: the route gate must match the strongest side effect of the handler
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test invoking the handler at each role level
