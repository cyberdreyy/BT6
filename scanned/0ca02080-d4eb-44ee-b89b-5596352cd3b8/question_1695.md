# Q1695: resource type confusion in csa_key.NewCSAKeyResources

## Question
Can an authenticated node user holding only the 'view' role cause `NewCSAKeyResources` at the JSON:API response of /v2/keys/csa to render one resource type with another's attribute set, exposing fields the intended presenter would redact?

## Target
- File/function: [core/web/presenters/csa_key.go](core/web/presenters/csa_key.go) -> `NewCSAKeyResources`
- Entrypoint: the JSON:API response of /v2/keys/csa
- Attacker controls: index vs export route selection (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `index vs export route selection` with a mismatched type/id.
- Invariant to test: the presenter selected must match the object type exactly
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over presenter selection for mismatched types
