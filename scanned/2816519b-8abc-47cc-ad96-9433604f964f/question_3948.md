# Q3948: error text discloses key or file paths in bridge_types_controller.ValidateBridgeType

## Question
Do errors from `ValidateBridgeType` at POST/PATCH/GET /v2/bridge_types reveal keystore paths, key ids, addresses or DB structure that let an authenticated node user holding only the 'edit' role (non-admin) target the next step of a key-theft chain?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `ValidateBridgeType`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: incoming/outgoing token fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `incoming/outgoing token fields`.
- Invariant to test: errors must not disclose key identities or filesystem layout
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies exclude paths and key ids
