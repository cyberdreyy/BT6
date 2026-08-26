# Q4360: credentials written to logs in middleware.Exists

## Question
Does the request logging path near `Exists` record password, token, secret or key-export fields submitted to GET on any static asset path served by ServeGzippedAssets/GzipFileServer, making them readable to anyone with log access obtained through a lower-severity bug?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Exists`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: percent-encoded and dot-segment path bytes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `percent-encoded and dot-segment path bytes` with credential-bearing field names not present in the redaction blacklist.
- Invariant to test: all credential-bearing fields must be redacted before logging
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the redaction helper with the full set of credential field names
