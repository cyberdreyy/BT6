# Q0476: shared MfaPromises across operations in RecoveryOAuthApi.ts

## Question
MfaPromises.rootPromise/submitPromise are single mutable refs shared by every operation; can an attacker start two operations so the MFA answer supplied for the low-value one satisfies the high-value one?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Start a benign operation and a signing operation, then resolve the submit promise once.
- Invariant to test: An MFA response must satisfy only the operation that requested it.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: start two RecoveryOAuthApi.generateURL-routed operations and assert one submitted code cannot complete both.
