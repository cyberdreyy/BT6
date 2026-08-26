# Q0717: static asset path traversal in cookies.FindSessionCookie

## Question
Can an unauthenticated HTTP client that can reach the node API port escape the embedded asset root through `FindSessionCookie` at the Cookie header on any authenticated /v2 route and read node files such as TLS keys, keystore files or the config secrets file?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: multiple clsession cookies in one header (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `multiple clsession cookies in one header` containing encoded dot-segments, backslashes or unicode separators.
- Invariant to test: asset serving must be confined to the embedded filesystem regardless of input encoding
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the path resolver with traversal payloads asserting no host file is opened
