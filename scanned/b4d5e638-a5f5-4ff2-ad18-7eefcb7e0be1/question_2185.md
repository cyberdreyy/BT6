# Q2185: double decoding of identifiers in helpers.jsonAPIError

## Question
Is an identifier decoded twice between the authorization check and the lookup on the path through `jsonAPIError`, letting an authenticated node user holding only the 'view' role authorize one object at the JSON:API response writer used by every /v2 controller and act on another?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `jsonAPIError`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: requested resource type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `requested resource type` percent-encoded so the two stages resolve to different values.
- Invariant to test: the value authorized and the value used must be byte-identical
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the authorized identifier equals the identifier passed to the store
