# Q0110: object identifier not ownership-scoped in bridge_types_controller.ValidateBridgeTypeNotExist

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) pass an identifier at POST/PATCH/GET /v2/bridge_types that makes `ValidateBridgeTypeNotExist` operate on an object outside their scope (another job, key, bridge, initiator, run)?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `ValidateBridgeTypeNotExist`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: incoming/outgoing token fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `incoming/outgoing token fields` referencing an object created by someone else.
- Invariant to test: handlers must scope lookups by the authenticated identity's entitlement
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test using foreign identifiers and asserting rejection
