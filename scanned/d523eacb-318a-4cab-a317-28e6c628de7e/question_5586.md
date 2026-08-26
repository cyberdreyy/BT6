# Q5586: empty or absent credential accepted in api.nextLink

## Question
Does `nextLink` treat an empty access key, empty secret or empty session id presented at page/size query parameters on /v2 index endpoints as a match against an unset/zero stored value, authenticating an authenticated node user holding only the 'view' role as a real identity?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `nextLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: JSON:API document fields in the request body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `JSON:API document fields in the request body` with empty or omitted credential fields.
- Invariant to test: empty credentials must always fail authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test with empty/absent credential fields asserting 401
