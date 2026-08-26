# Q2356: route role weaker than the side effect in evm_transfer_controller.CreateWithRelayer

## Question
Is the route reaching `CreateWithRelayer` at POST /v2/transfers/evm gated by a role weaker than the effect it produces, letting an authenticated node user holding only the 'edit' role (non-admin) cause it?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateWithRelayer`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: evmChainID (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `evmChainID` from the weakest session the route accepts.
- Invariant to test: the route gate must match the strongest side effect of the handler
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test invoking the handler at each role level
