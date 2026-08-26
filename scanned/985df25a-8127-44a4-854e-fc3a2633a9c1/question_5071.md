# Q5071: content-encoding negotiation file selection in auth.AuthenticateExternalInitiator

## Question
Can a holder of a restricted API access-key/secret pair steer the file chosen by `AuthenticateExternalInitiator` via encoding negotiation on any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list so a file outside the intended asset set is served?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateExternalInitiator`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: external-initiator accessKey/secret headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Combine `external-initiator accessKey/secret headers` with crafted Accept-Encoding values that make the server append a suffix to an attacker-chosen path.
- Invariant to test: negotiation may only select among pre-registered asset variants
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test over findBestFile/negotiateContentEncoding with hostile paths and encodings
