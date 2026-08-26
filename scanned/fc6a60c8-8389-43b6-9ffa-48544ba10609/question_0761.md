# Q0761: credential returned after creation in external_initiator.NewExternalInitiator

## Question
Does the create path through `NewExternalInitiator` at the external-initiator authenticated route POST /v2/jobs/:ID/runs return or persist the credential in a form readable later by a holder of an external-initiator access-key/secret pair at a lower role?

## Target
- File/function: [core/bridges/external_initiator.go](core/bridges/external_initiator.go) -> `NewExternalInitiator`
- Entrypoint: the external-initiator authenticated route POST /v2/jobs/:ID/runs
- Attacker controls: the accessKey/secret headers (attacker capability: a holder of an external-initiator access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Create with `accessKey/secret headers`, then read the object back.
- Invariant to test: credentials are shown once and stored hashed
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: round-trip test asserting the secret is unreadable after creation
