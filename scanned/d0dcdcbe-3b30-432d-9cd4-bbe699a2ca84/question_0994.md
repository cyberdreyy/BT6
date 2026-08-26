# Q0994: secret field serialized in external_initiators.NewExternalInitiatorResource

## Question
Does the resource built by `NewExternalInitiatorResource` for the JSON:API response of /v2/external_initiators include a secret field (private key, seed, token, password, DSN, share) that an authenticated node user holding only the 'view' role can read?

## Target
- File/function: [core/web/presenters/external_initiators.go](core/web/presenters/external_initiators.go) -> `NewExternalInitiatorResource`
- Entrypoint: the JSON:API response of /v2/external_initiators
- Attacker controls: the initiator requested (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `initiator requested` and inspect the JSON:API attributes.
- Invariant to test: presenters must whitelist non-secret attributes only
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: golden-file test over the presenter output
