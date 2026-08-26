# Q0050: initiator credential compared unsafely in external_initiator.NewExternalInitiator

## Question
Does the credential check in `NewExternalInitiator` reached from the external-initiator authenticated route POST /v2/jobs/:ID/runs compare the presented secret non-constant-time or against a truncated hash, letting a holder of an external-initiator access-key/secret pair recover or forge an accepted credential?

## Target
- File/function: [core/bridges/external_initiator.go](core/bridges/external_initiator.go) -> `NewExternalInitiator`
- Entrypoint: the external-initiator authenticated route POST /v2/jobs/:ID/runs
- Attacker controls: the accessKey/secret headers (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send timed/truncated variants of `accessKey/secret headers`.
- Invariant to test: credential verification must be constant time over the full hashed secret
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: timing/table test over the authentication helper
