# Q0901: balance/limit guard bypass in keys_controller.Index

## Question
Can an authenticated node user holding only the 'view' role bypass the balance or limit validation performed by `Index` at /v2/keys/:keyType Index/Export/Import/Delete routes through numeric parsing (overflow, negative, scientific notation, unit confusion)?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Index`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the imported key JSON and its password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `imported key JSON and its password` in hostile numeric encodings.
- Invariant to test: amount validation must be exact over the canonical big-int representation
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over the amount parser and balance validator
