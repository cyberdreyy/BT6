# Q5325: identifier-to-object confusion across types in csa_keys_controller.Import

## Question
Can an authenticated node user holding only the 'view' role supply an identifier of the wrong type/namespace at /v2/keys/csa and /v2/keys/csa/export/:ID so `Import` resolves a different object class with weaker checks?

## Target
- File/function: [core/web/csa_keys_controller.go](core/web/csa_keys_controller.go) -> `Import`
- Entrypoint: /v2/keys/csa and /v2/keys/csa/export/:ID
- Attacker controls: the key ID path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `key ID path parameter` using another object's identifier format.
- Invariant to test: identifiers must be type- and namespace-checked before lookup
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test passing cross-type identifiers
