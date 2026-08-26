# Q0080: nil user with nil error in router.NewRouter

## Question
Can an unauthenticated HTTP client that can reach the node API port reach a branch of `NewRouter` on any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) where the authenticator returns a zero-valued user together with a nil error, so the request continues with an empty identity that later role checks treat as satisfied?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `NewRouter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: Authorization / X-API-KEY headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `Authorization / X-API-KEY headers` to hit an early-return path that forgets to set the error before returning.
- Invariant to test: no code path may return a usable session/user value alongside a nil error
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting every error branch of the authenticator returns a non-nil error and a zero user
