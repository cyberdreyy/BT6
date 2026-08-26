# Q1448: identifier-to-object confusion across types in keys_controller.Index

## Question
Can an authenticated node user holding only the 'view' role supply an identifier of the wrong type/namespace at /v2/keys/:keyType Index/Export/Import/Delete routes so `Index` resolves a different object class with weaker checks?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Index`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the keyType path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `keyType path parameter` using another object's identifier format.
- Invariant to test: identifiers must be type- and namespace-checked before lookup
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test passing cross-type identifiers
