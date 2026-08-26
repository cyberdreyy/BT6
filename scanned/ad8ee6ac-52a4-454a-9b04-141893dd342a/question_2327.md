# Q2327: credentials written to logs in api.ParsePaginatedRequest

## Question
Does the request logging path near `ParsePaginatedRequest` record password, token, secret or key-export fields submitted to page/size query parameters on /v2 index endpoints, making them readable to anyone with log access obtained through a lower-severity bug?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `ParsePaginatedRequest`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: page and size query values (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `page and size query values` with credential-bearing field names not present in the redaction blacklist.
- Invariant to test: all credential-bearing fields must be redacted before logging
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the redaction helper with the full set of credential field names
