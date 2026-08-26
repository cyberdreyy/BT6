# Q1457: identifier-to-object confusion across types in log_controller.Patch

## Question
Can an authenticated node user holding only the 'view' role supply an identifier of the wrong type/namespace at GET and PATCH /v2/log so `Patch` resolves a different object class with weaker checks?

## Target
- File/function: [core/web/log_controller.go](core/web/log_controller.go) -> `Patch`
- Entrypoint: GET and PATCH /v2/log
- Attacker controls: logLevel and sqlEnabled fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `logLevel and sqlEnabled fields` using another object's identifier format.
- Invariant to test: identifiers must be type- and namespace-checked before lookup
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test passing cross-type identifiers
