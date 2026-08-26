# Q1154: initiator credential compared unsafely in bridge_type.AuthenticateBridgeType

## Question
Does the credential check in `AuthenticateBridgeType` reached from bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs compare the presented secret non-constant-time or against a truncated hash, letting a holder of an external-initiator access-key/secret pair recover or forge an accepted credential?

## Target
- File/function: [core/bridges/bridge_type.go](core/bridges/bridge_type.go) -> `AuthenticateBridgeType`
- Entrypoint: bridge authentication and bridge type (de)serialization reached from /v2/bridge_types and job runs
- Attacker controls: the bridge JSON body (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send timed/truncated variants of `bridge JSON body`.
- Invariant to test: credential verification must be constant time over the full hashed secret
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: timing/table test over the authentication helper
