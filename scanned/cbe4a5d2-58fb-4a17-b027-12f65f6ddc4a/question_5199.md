# Q5199: directory metacharacter injection in identity lookup in webauthn_controller.FinishRegistration

## Question
Can an authenticated node user holding only the 'view' role inject filter metacharacters through `FinishRegistration` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) so the identity query matches an administrator entry instead of the submitted account?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `FinishRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: webauthn session store cookie (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `webauthn session store cookie` containing filter/DN metacharacters.
- Invariant to test: all externally supplied values must be escaped before entering the identity query
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the query builder with metacharacter payloads
