# Q1503: empty or absent credential accepted in helpers.jsonAPIError

## Question
Does `jsonAPIError` treat an empty access key, empty secret or empty session id presented at the JSON:API response writer used by every /v2 controller as a match against an unset/zero stored value, authenticating an authenticated node user holding only the 'view' role as a real identity?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `jsonAPIError`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: requested resource type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `requested resource type` with empty or omitted credential fields.
- Invariant to test: empty credentials must always fail authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test with empty/absent credential fields asserting 401
