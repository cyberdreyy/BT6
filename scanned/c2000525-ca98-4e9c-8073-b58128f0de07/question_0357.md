# Q0357: import path plants attacker key material in log_controller.Patch

## Question
Can an authenticated node user holding only the 'view' role import key material through `Patch` at GET and PATCH /v2/log so the node later signs oracle reports or transactions with an attacker-known key?

## Target
- File/function: [core/web/log_controller.go](core/web/log_controller.go) -> `Patch`
- Entrypoint: GET and PATCH /v2/log
- Attacker controls: logLevel and sqlEnabled fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `logLevel and sqlEnabled fields` containing a key the attacker generated.
- Invariant to test: key import must be admin-only and validated
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test importing a key from a non-admin session
