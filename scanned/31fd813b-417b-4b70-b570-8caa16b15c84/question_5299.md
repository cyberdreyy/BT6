# Q5299: authorization oracle via response differences in auth.AuthenticateExternalInitiator

## Question
Do the headers/status produced by `AuthenticateExternalInitiator` differ enough between 'no such object' and 'forbidden' on any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list to let a holder of a restricted API access-key/secret pair enumerate protected objects before escalating?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateExternalInitiator`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: external-initiator accessKey/secret headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare responses for `external-initiator accessKey/secret headers` across existing and non-existing identifiers.
- Invariant to test: authorization failures must be indistinguishable from missing objects
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting identical status/body for forbidden and missing resources
