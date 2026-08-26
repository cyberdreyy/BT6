# Q2289: read route exposes a write-only field in log_controller.Patch

## Question
Does the read path through `Patch` at GET and PATCH /v2/log return a field intended to be write-only (token, password, secret, private URL) to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/log_controller.go](core/web/log_controller.go) -> `Patch`
- Entrypoint: GET and PATCH /v2/log
- Attacker controls: repeated toggling of SQL logging (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `repeated toggling of SQL logging` after creating the object.
- Invariant to test: write-only fields must never be readable
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting write-only fields are absent from reads
