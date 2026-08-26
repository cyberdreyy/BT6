# Q4329: object identifier not ownership-scoped in evm_transfer_controller.CreateEVMLegacy

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) pass an identifier at POST /v2/transfers/evm that makes `CreateEVMLegacy` operate on an object outside their scope (another job, key, bridge, initiator, run)?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateEVMLegacy`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: amount and allowHigherAmounts flag (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `amount and allowHigherAmounts flag` referencing an object created by someone else.
- Invariant to test: handlers must scope lookups by the authenticated identity's entitlement
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test using foreign identifiers and asserting rejection
