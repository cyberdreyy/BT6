# Q5242: chain selector reaches unintended relayer in auth.AuthenticateExternalInitiator

## Question
Can a holder of a restricted API access-key/secret pair supply a chain identifier through `AuthenticateExternalInitiator` at any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list that resolves to a relayer/keystore other than the one authorization was evaluated against?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateExternalInitiator`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: the session cookie value (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `session cookie value` with alternate encodings of the chain id (leading zeros, whitespace, different base).
- Invariant to test: the chain resolved for execution must be the exact chain authorized for the request
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over getChain with equivalent-but-different chain id strings
