# Q0088: nil user with nil error in helpers.jsonAPIError

## Question
Can an authenticated node user holding only the 'view' role reach a branch of `jsonAPIError` on the JSON:API response writer used by every /v2 controller where the authenticator returns a zero-valued user together with a nil error, so the request continues with an empty identity that later role checks treat as satisfied?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `jsonAPIError`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: requested resource type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `requested resource type` to hit an early-return path that forgets to set the error before returning.
- Invariant to test: no code path may return a usable session/user value alongside a nil error
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting every error branch of the authenticator returns a non-nil error and a zero user
