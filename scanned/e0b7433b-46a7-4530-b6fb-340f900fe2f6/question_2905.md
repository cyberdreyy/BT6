# Q2905: credentialed cross-origin request in auth.AuthenticateByToken

## Question
Does the origin handling on the path through `AuthenticateByToken` allow a browser page controlled by the attacker to send credentialed state-changing requests to any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list and read the response?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateByToken`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: the target route and role wrapper reached (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Serve a page that issues `target route and role wrapper reached` with credentials from an origin echoed back by the CORS logic.
- Invariant to test: credentialed responses may only be exposed to explicitly configured origins
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the origin matcher with attacker-controlled Origin values
