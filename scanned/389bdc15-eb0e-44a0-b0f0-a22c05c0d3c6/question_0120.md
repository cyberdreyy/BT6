# Q0120: object identifier not ownership-scoped in log_controller.Patch

## Question
Can an authenticated node user holding only the 'view' role pass an identifier at GET and PATCH /v2/log that makes `Patch` operate on an object outside their scope (another job, key, bridge, initiator, run)?

## Target
- File/function: [core/web/log_controller.go](core/web/log_controller.go) -> `Patch`
- Entrypoint: GET and PATCH /v2/log
- Attacker controls: repeated toggling of SQL logging (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeated toggling of SQL logging` referencing an object created by someone else.
- Invariant to test: handlers must scope lookups by the authenticated identity's entitlement
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test using foreign identifiers and asserting rejection
