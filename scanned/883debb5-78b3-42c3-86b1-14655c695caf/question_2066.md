# Q2066: chain id selects an unauthorized keystore in workflow_keys_controller.Index

## Question
Can an authenticated node user holding only the 'view' role pick a chain identifier at GET /v2/keys/workflow that makes `Index` use a key or relayer outside the authorized set, signing with an unintended node key?

## Target
- File/function: [core/web/workflow_keys_controller.go](core/web/workflow_keys_controller.go) -> `Index`
- Entrypoint: GET /v2/keys/workflow
- Attacker controls: the request path and query parameters (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `request path and query parameters` with an alternate/unknown chain id.
- Invariant to test: the key/relayer used must be derived from validated, authorized chain configuration
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the selected keystore for hostile chain ids
