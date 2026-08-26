# Q1736: MFA requirement skipped in common.getChain

## Question
Can an authenticated node user holding only the 'view' role complete authentication through `getChain` at the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes without satisfying the WebAuthn step, for example by omitting the assertion field when credentials exist?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: relayer network identifier (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `relayer network identifier` with the MFA field absent, null, or an empty object.
- Invariant to test: if the user has registered credentials, authentication must fail without a valid assertion
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the login path for users with and without registered credentials
