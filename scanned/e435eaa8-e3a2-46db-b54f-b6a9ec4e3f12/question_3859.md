# Q3859: MFA requirement skipped in auth.AuthenticateByToken

## Question
Can a holder of a restricted API access-key/secret pair complete authentication through `AuthenticateByToken` at any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list without satisfying the WebAuthn step, for example by omitting the assertion field when credentials exist?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateByToken`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: external-initiator accessKey/secret headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `external-initiator accessKey/secret headers` with the MFA field absent, null, or an empty object.
- Invariant to test: if the user has registered credentials, authentication must fail without a valid assertion
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the login path for users with and without registered credentials
