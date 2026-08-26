# Q4365: credentials written to logs in helpers.paginatedResponse

## Question
Does the request logging path near `paginatedResponse` record password, token, secret or key-export fields submitted to the JSON:API response writer used by every /v2 controller, making them readable to anyone with log access obtained through a lower-severity bug?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedResponse`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: pagination parameters (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `pagination parameters` with credential-bearing field names not present in the redaction blacklist.
- Invariant to test: all credential-bearing fields must be redacted before logging
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the redaction helper with the full set of credential field names
