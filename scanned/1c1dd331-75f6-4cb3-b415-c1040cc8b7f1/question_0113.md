# Q0113: object identifier not ownership-scoped in csa_keys_controller.Index

## Question
Can an authenticated node user holding only the 'view' role pass an identifier at /v2/keys/csa and /v2/keys/csa/export/:ID that makes `Index` operate on an object outside their scope (another job, key, bridge, initiator, run)?

## Target
- File/function: [core/web/csa_keys_controller.go](core/web/csa_keys_controller.go) -> `Index`
- Entrypoint: /v2/keys/csa and /v2/keys/csa/export/:ID
- Attacker controls: the export password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `export password` referencing an object created by someone else.
- Invariant to test: handlers must scope lookups by the authenticated identity's entitlement
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test using foreign identifiers and asserting rejection
