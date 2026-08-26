# Q2062: chain id selects an unauthorized keystore in keys_controller.Index

## Question
Can an authenticated node user holding only the 'view' role pick a chain identifier at /v2/keys/:keyType Index/Export/Import/Delete routes that makes `Index` use a key or relayer outside the authorized set, signing with an unintended node key?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Index`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the imported key JSON and its password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `imported key JSON and its password` with an alternate/unknown chain id.
- Invariant to test: the key/relayer used must be derived from validated, authorized chain configuration
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the selected keystore for hostile chain ids
