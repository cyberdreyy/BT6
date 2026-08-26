# Q0246: role string comparison weakness in helpers.jsonAPIError

## Question
Can an authenticated node user holding only the 'view' role obtain a role value that passes the comparison performed on the path through `jsonAPIError` (case, whitespace or prefix handling) even though the stored role is lower-privileged?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `jsonAPIError`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: inputs that select the error branch (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `inputs that select the error branch` so the role string persisted or parsed differs in case/whitespace from the constant compared at the gate.
- Invariant to test: role comparison must be exact-match over the canonical role enum
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test feeding role variants ('Admin', ' admin', 'admin\n') through the role check
