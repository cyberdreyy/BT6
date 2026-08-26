# Q2323: credentials written to logs in auth.AuthenticateBySession

## Question
Does the request logging path near `AuthenticateBySession` record password, token, secret or key-export fields submitted to any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list, making them readable to anyone with log access obtained through a lower-severity bug?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateBySession`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: external-initiator accessKey/secret headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `external-initiator accessKey/secret headers` with credential-bearing field names not present in the redaction blacklist.
- Invariant to test: all credential-bearing fields must be redacted before logging
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the redaction helper with the full set of credential field names
