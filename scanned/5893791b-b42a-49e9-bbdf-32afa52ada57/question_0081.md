# Q0081: nil user with nil error in middleware.NewEmbedFileSystem

## Question
Can an unauthenticated HTTP client that can reach the node API port reach a branch of `NewEmbedFileSystem` on GET on any static asset path served by ServeGzippedAssets/GzipFileServer where the authenticator returns a zero-valued user together with a nil error, so the request continues with an empty identity that later role checks treat as satisfied?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `NewEmbedFileSystem`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: Accept-Encoding negotiation (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `Accept-Encoding negotiation` to hit an early-return path that forgets to set the error before returning.
- Invariant to test: no code path may return a usable session/user value alongside a nil error
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting every error branch of the authenticator returns a non-nil error and a zero user
