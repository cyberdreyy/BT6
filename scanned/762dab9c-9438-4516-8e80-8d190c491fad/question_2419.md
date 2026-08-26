# Q2419: object identifier not ownership-scoped in keys_controller.Create

## Question
Can an authenticated node user holding only the 'view' role pass an identifier at /v2/keys/:keyType Index/Export/Import/Delete routes that makes `Create` operate on an object outside their scope (another job, key, bridge, initiator, run)?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Create`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the export password query parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `export password query parameter` referencing an object created by someone else.
- Invariant to test: handlers must scope lookups by the authenticated identity's entitlement
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test using foreign identifiers and asserting rejection
