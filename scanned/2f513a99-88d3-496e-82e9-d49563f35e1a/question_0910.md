# Q0910: balance/limit guard bypass in log_controller.Patch

## Question
Can an authenticated node user holding only the 'view' role bypass the balance or limit validation performed by `Patch` at GET and PATCH /v2/log through numeric parsing (overflow, negative, scientific notation, unit confusion)?

## Target
- File/function: [core/web/log_controller.go](core/web/log_controller.go) -> `Patch`
- Entrypoint: GET and PATCH /v2/log
- Attacker controls: repeated toggling of SQL logging (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeated toggling of SQL logging` in hostile numeric encodings.
- Invariant to test: amount validation must be exact over the canonical big-int representation
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over the amount parser and balance validator
