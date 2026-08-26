# Q0916: secret in relationship/included documents in external_initiators.NewExternalInitiatorAuthentication

## Question
Does the JSON:API relationship or included section produced around `NewExternalInitiatorAuthentication` at the JSON:API response of /v2/external_initiators carry secret attributes of related objects to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/presenters/external_initiators.go](core/web/presenters/external_initiators.go) -> `NewExternalInitiatorAuthentication`
- Entrypoint: the JSON:API response of /v2/external_initiators
- Attacker controls: create vs index route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `create vs index route selection` with include parameters.
- Invariant to test: included resources must be redacted like primary resources
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting included documents pass the same redaction
