# Q5322: identifier-to-object confusion across types in bridge_types_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) supply an identifier of the wrong type/namespace at POST/PATCH/GET /v2/bridge_types so `Create` resolves a different object class with weaker checks?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `Create`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: bridge name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `bridge name and URL` using another object's identifier format.
- Invariant to test: identifiers must be type- and namespace-checked before lookup
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test passing cross-type identifiers
