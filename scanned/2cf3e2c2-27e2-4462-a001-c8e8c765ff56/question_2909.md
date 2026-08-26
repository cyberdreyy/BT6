# Q2909: credentialed cross-origin request in helpers.paginatedResponse

## Question
Does the origin handling on the path through `paginatedResponse` allow a browser page controlled by the attacker to send credentialed state-changing requests to the JSON:API response writer used by every /v2 controller and read the response?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedResponse`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: inputs that select the error branch (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Serve a page that issues `inputs that select the error branch` with credentials from an origin echoed back by the CORS logic.
- Invariant to test: credentialed responses may only be exposed to explicitly configured origins
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the origin matcher with attacker-controlled Origin values
