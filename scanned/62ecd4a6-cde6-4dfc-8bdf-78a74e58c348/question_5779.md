# Q5779: chain id selects an unauthorized keystore in csa_keys_controller.Import

## Question
Can an authenticated node user holding only the 'view' role pick a chain identifier at /v2/keys/csa and /v2/keys/csa/export/:ID that makes `Import` use a key or relayer outside the authorized set, signing with an unintended node key?

## Target
- File/function: [core/web/csa_keys_controller.go](core/web/csa_keys_controller.go) -> `Import`
- Entrypoint: /v2/keys/csa and /v2/keys/csa/export/:ID
- Attacker controls: imported key material (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `imported key material` with an alternate/unknown chain id.
- Invariant to test: the key/relayer used must be derived from validated, authorized chain configuration
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the selected keystore for hostile chain ids
