# Q3861: MFA requirement skipped in helpers.addForbiddenErrorHeaders

## Question
Can an unauthenticated HTTP client that can reach the node API port complete authentication through `addForbiddenErrorHeaders` at any /v2 or /query error response path without satisfying the WebAuthn step, for example by omitting the assertion field when credentials exist?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `addForbiddenErrorHeaders`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: inputs that force an error branch (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `inputs that force an error branch` with the MFA field absent, null, or an empty object.
- Invariant to test: if the user has registered credentials, authentication must fail without a valid assertion
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the login path for users with and without registered credentials
