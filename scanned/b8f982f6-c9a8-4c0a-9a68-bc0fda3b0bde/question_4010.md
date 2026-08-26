# Q4010: chain id selects an unauthorized keystore in external_initiators_controller.Index

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) pick a chain identifier at POST/DELETE /v2/external_initiators that makes `Index` use a key or relayer outside the authorized set, signing with an unintended node key?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `Index`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: duplicate/colliding names (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `duplicate/colliding names` with an alternate/unknown chain id.
- Invariant to test: the key/relayer used must be derived from validated, authorized chain configuration
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the selected keystore for hostile chain ids
