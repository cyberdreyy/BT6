# Q5751: MFA requirement skipped in router.rateLimiter

## Question
Can an unauthenticated HTTP client that can reach the node API port complete authentication through `rateLimiter` at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) without satisfying the WebAuthn step, for example by omitting the assertion field when credentials exist?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `rateLimiter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: Authorization / X-API-KEY headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `Authorization / X-API-KEY headers` with the MFA field absent, null, or an empty object.
- Invariant to test: if the user has registered credentials, authentication must fail without a valid assertion
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the login path for users with and without registered credentials
