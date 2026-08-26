# Q5321: identifier-to-object confusion across types in external_initiators_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) supply an identifier of the wrong type/namespace at POST/DELETE /v2/external_initiators so `Create` resolves a different object class with weaker checks?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `Create`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: the initiator name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `initiator name and URL` using another object's identifier format.
- Invariant to test: identifiers must be type- and namespace-checked before lookup
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test passing cross-type identifiers
