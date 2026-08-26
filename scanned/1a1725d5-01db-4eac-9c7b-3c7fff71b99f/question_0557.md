# Q0557: credentialed cross-origin request in gql.AuthenticateGQL

## Question
Does the origin handling on the path through `AuthenticateGQL` allow a browser page controlled by the attacker to send credentialed state-changing requests to POST /query (GraphQL) guarded by AuthenticateGQL and read the response?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `AuthenticateGQL`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: batched operations in one request (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Serve a page that issues `batched operations in one request` with credentials from an origin echoed back by the CORS logic.
- Invariant to test: credentialed responses may only be exposed to explicitly configured origins
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the origin matcher with attacker-controlled Origin values
