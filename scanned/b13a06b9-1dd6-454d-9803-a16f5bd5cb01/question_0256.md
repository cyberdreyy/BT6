# Q0256: timeout resolves the root promise in RecoveryOAuthApi.ts

## Question
withMfa rejects the root MFA promise on timeout but the loop continues with the next attempt; can an attacker use a 300000ms timeout window in RecoveryOAuthApi.generateURL to keep an operation alive after the user cancelled?

## Target
- File/function: [src/client/recovery/RecoveryOAuthApi.ts](src/client/recovery/RecoveryOAuthApi.ts) - RecoveryOAuthApi.generateURL, authorize (shares privy:state_code / privy:code_verifier with login OAuth)
- Entrypoint: privy.recovery.auth.generateURL(redirectTo) then authorize(code, state)
- Attacker controls: redirect_to, returned code/state, interleaving with privy.auth.oauth flows
- Exploit idea: Let the MFA wait time out and observe the retry behaviour and promise state.
- Invariant to test: A cancelled or timed-out MFA challenge must terminate the operation, not roll to another attempt.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: force a timeout in RecoveryOAuthApi.generateURL and assert the operation rejects immediately.
