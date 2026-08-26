# Q0914: secret in relationship/included documents in csa_key.NewCSAKeyResource

## Question
Does the JSON:API relationship or included section produced around `NewCSAKeyResource` at the JSON:API response of /v2/keys/csa carry secret attributes of related objects to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/presenters/csa_key.go](core/web/presenters/csa_key.go) -> `NewCSAKeyResource`
- Entrypoint: the JSON:API response of /v2/keys/csa
- Attacker controls: index vs export route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `index vs export route selection` with include parameters.
- Invariant to test: included resources must be redacted like primary resources
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: test asserting included documents pass the same redaction
