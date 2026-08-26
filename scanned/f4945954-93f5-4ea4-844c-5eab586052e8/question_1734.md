# Q1734: MFA requirement skipped in cookies.FindSessionCookie

## Question
Can an unauthenticated HTTP client that can reach the node API port complete authentication through `FindSessionCookie` at the Cookie header on any authenticated /v2 route without satisfying the WebAuthn step, for example by omitting the assertion field when credentials exist?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie name casing and attributes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `cookie name casing and attributes` with the MFA field absent, null, or an empty object.
- Invariant to test: if the user has registered credentials, authentication must fail without a valid assertion
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the login path for users with and without registered credentials
