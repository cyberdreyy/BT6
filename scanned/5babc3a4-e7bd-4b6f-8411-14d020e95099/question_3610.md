# Q3610: state-changing request without origin binding in api.paginationLink

## Question
Can a page loaded by a logged-in operator cause an authenticated node user holding only the 'view' role's chosen state change at page/size query parameters on /v2 index endpoints through `paginationLink` because the session cookie alone authorizes the mutation?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `paginationLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: JSON:API document fields in the request body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Auto-submit `JSON:API document fields in the request body` from an attacker page targeting a key-export or transfer route.
- Invariant to test: state-changing requests must require a non-cookie credential or origin binding
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test issuing a cross-site style request with only a session cookie
