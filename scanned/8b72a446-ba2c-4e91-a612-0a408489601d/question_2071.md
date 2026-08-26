# Q2071: chain id selects an unauthorized keystore in log_controller.Patch

## Question
Can an authenticated node user holding only the 'view' role pick a chain identifier at GET and PATCH /v2/log that makes `Patch` use a key or relayer outside the authorized set, signing with an unintended node key?

## Target
- File/function: [core/web/log_controller.go](core/web/log_controller.go) -> `Patch`
- Entrypoint: GET and PATCH /v2/log
- Attacker controls: logLevel and sqlEnabled fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `logLevel and sqlEnabled fields` with an alternate/unknown chain id.
- Invariant to test: the key/relayer used must be derived from validated, authorized chain configuration
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the selected keystore for hostile chain ids
