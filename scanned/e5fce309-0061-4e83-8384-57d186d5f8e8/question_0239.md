# Q0239: role string comparison weakness in middleware.NewEmbedFileSystem

## Question
Can an unauthenticated HTTP client that can reach the node API port obtain a role value that passes the comparison performed on the path through `NewEmbedFileSystem` (case, whitespace or prefix handling) even though the stored role is lower-privileged?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `NewEmbedFileSystem`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: range and conditional headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `range and conditional headers` so the role string persisted or parsed differs in case/whitespace from the constant compared at the gate.
- Invariant to test: role comparison must be exact-match over the canonical role enum
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test feeding role variants ('Admin', ' admin', 'admin\n') through the role check
