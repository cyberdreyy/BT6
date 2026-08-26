# Q1535: concurrent submissions break a uniqueness guard in log_controller.Patch

## Question
Can an authenticated node user holding only the 'view' role race two requests through `Patch` at GET and PATCH /v2/log so a uniqueness or single-use guard is defeated (duplicate initiator, duplicate bridge, double run, double transfer)?

## Target
- File/function: [core/web/log_controller.go](core/web/log_controller.go) -> `Patch`
- Entrypoint: GET and PATCH /v2/log
- Attacker controls: repeated toggling of SQL logging (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `repeated toggling of SQL logging`.
- Invariant to test: guards must be enforced by a transactional/unique constraint
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: concurrent handler test asserting exactly one success
