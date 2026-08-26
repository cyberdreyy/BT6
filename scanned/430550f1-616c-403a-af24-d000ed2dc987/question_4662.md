# Q4662: role string comparison weakness in auth.AuthenticateExternalInitiator

## Question
Can a holder of a restricted API access-key/secret pair obtain a role value that passes the comparison performed on the path through `AuthenticateExternalInitiator` (case, whitespace or prefix handling) even though the stored role is lower-privileged?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateExternalInitiator`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: the target route and role wrapper reached (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `target route and role wrapper reached` so the role string persisted or parsed differs in case/whitespace from the constant compared at the gate.
- Invariant to test: role comparison must be exact-match over the canonical role enum
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test feeding role variants ('Admin', ' admin', 'admin\n') through the role check
