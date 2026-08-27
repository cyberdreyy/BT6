# Q4921: balance/limit guard bypass in external_initiators_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) bypass the balance or limit validation performed by `Create` at POST/DELETE /v2/external_initiators through numeric parsing (overflow, negative, scientific notation, unit confusion)?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `Create`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: duplicate/colliding names (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `duplicate/colliding names` in hostile numeric encodings.
- Invariant to test: amount validation must be exact over the canonical big-int representation
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over the amount parser and balance validator
