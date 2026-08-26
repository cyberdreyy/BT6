# Q1580: external-initiator credential over-scoped in common.getChain

## Question
Can an authenticated node user holding only the 'view' role use an external-initiator credential accepted by `getChain` on the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes to reach routes beyond the single job-run endpoint it was issued for?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: chain id formatting (leading zeros, alternate base) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `chain id formatting (leading zeros, alternate base)` against other /v2 routes sharing the authenticator list.
- Invariant to test: an EI credential must authorize only run-triggering for jobs bound to that initiator
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: route test presenting EI credentials against every /v2 route
