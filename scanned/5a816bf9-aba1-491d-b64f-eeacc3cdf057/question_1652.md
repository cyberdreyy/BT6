# Q1652: identity overwritten downstream in middleware.NewEmbedFileSystem

## Question
Can a later middleware or handler on the path through `NewEmbedFileSystem` overwrite the authenticated identity established at GET on any static asset path served by ServeGzippedAssets/GzipFileServer using a request-controlled field?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `NewEmbedFileSystem`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: Accept-Encoding negotiation (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Include `Accept-Encoding negotiation` whose name collides with the context key or session field used downstream.
- Invariant to test: the authenticated identity must be immutable after the auth middleware
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test injecting colliding body/header fields and asserting the identity is unchanged
