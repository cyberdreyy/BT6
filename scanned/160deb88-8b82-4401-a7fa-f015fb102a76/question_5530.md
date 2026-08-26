# Q5530: state-changing request without origin binding in helpers.paginatedRequest

## Question
Can a page loaded by a logged-in operator cause an authenticated node user holding only the 'view' role's chosen state change at the JSON:API response writer used by every /v2 controller through `paginatedRequest` because the session cookie alone authorizes the mutation?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedRequest`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: requested resource type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Auto-submit `requested resource type` from an attacker page targeting a key-export or transfer route.
- Invariant to test: state-changing requests must require a non-cookie credential or origin binding
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test issuing a cross-site style request with only a session cookie
