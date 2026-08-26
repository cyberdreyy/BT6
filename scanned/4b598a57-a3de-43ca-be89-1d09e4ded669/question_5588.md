# Q5588: redirect target attacker-controlled in authentication.AuthenticationProvider

## Question
Can an unauthenticated HTTP client that can reach the node API port steer the post-authentication redirect handled near `AuthenticationProvider` at POST /sessions and every AuthenticationProvider call behind /v2 auth to an external host, capturing the issued session cookie or code?

## Target
- File/function: [core/sessions/authentication.go](core/sessions/authentication.go) -> `AuthenticationProvider`
- Entrypoint: POST /sessions and every AuthenticationProvider call behind /v2 auth
- Attacker controls: WebAuthn assertion payload (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Supply `WebAuthn assertion payload` with an absolute or protocol-relative URL.
- Invariant to test: redirect targets must be restricted to a server-side allowlist
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the redirect validator with hostile URLs
