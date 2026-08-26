# Q0166: role wrapper omitted on a route in common.getChain

## Question
Is there a state-changing route reaching `getChain` from the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes that is registered without RequiresEditRole/RequiresAdminRole, letting an authenticated node user holding only the 'view' role invoke it with only view or run rights?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: chain id formatting (leading zeros, alternate base) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Enumerate registered routes and compare each handler's declared minimum role against its wrapper, then call the weakest one with `chain id formatting (leading zeros, alternate base)`.
- Invariant to test: every state-changing /v2 route must be wrapped by the role gate matching its side effect
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: reflective route-table test asserting each non-GET route carries a role middleware
