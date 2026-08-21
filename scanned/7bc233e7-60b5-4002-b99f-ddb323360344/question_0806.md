# Q0806: clearMfa userId is caller supplied in RecoveryOAuthApi.ts

## Question
clearMfa forwards the caller's userId to the iframe; can an attacker pass another user's id through RecoveryOAuthApi.generateURL to drop MFA state that is not theirs?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Call the clear path with a foreign user id.
- Invariant to test: MFA clearing must be scoped to the authenticated session's own user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call RecoveryOAuthApi.generateURL with a foreign userId and assert the session's own id is used or the call is refused.
