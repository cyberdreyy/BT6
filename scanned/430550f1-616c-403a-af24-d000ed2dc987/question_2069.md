# Q2069: chain id selects an unauthorized keystore in replay_controller.ReplayFromBlock

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) pick a chain identifier at POST /v2/replay_from_block/:number that makes `ReplayFromBlock` use a key or relayer outside the authorized set, signing with an unintended node key?

## Target
- File/function: [core/web/replay_controller.go](core/web/replay_controller.go) -> `ReplayFromBlock`
- Entrypoint: POST /v2/replay_from_block/:number
- Attacker controls: the block number path parameter (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `block number path parameter` with an alternate/unknown chain id.
- Invariant to test: the key/relayer used must be derived from validated, authorized chain configuration
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the selected keystore for hostile chain ids
