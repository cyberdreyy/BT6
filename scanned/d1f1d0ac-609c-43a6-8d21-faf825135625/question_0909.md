# Q0909: balance/limit guard bypass in config_controller.Show

## Question
Can an authenticated node user holding only the 'view' role bypass the balance or limit validation performed by `Show` at GET /v2/config/v2 through numeric parsing (overflow, negative, scientific notation, unit confusion)?

## Target
- File/function: [core/web/config_controller.go](core/web/config_controller.go) -> `Show`
- Entrypoint: GET /v2/config/v2
- Attacker controls: Accept header / response format (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `Accept header / response format` in hostile numeric encodings.
- Invariant to test: amount validation must be exact over the canonical big-int representation
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over the amount parser and balance validator
