# Q0472: shared MfaPromises across operations in MfaApi.ts

## Question
MfaPromises.rootPromise/submitPromise are single mutable refs shared by every operation; can an attacker start two operations so the MFA answer supplied for the low-value one satisfies the high-value one?

## Target
- File/function: [src/client/mfa/MfaApi.ts](src/client/mfa/MfaApi.ts) - MfaApi.verifyMfa, initEnrollMfa, submitEnrollMfa, unenrollMfa, unlinkPasskey, clearMfa
- Entrypoint: privy.mfa.unenrollMfa(method) / privy.mfa.clearMfa({userId})
- Attacker controls: method argument, credentialId, removeAsMfa, userId, call ordering against refreshSession
- Exploit idea: Start a benign operation and a signing operation, then resolve the submit promise once.
- Invariant to test: An MFA response must satisfy only the operation that requested it.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: start two MfaApi.verifyMfa-routed operations and assert one submitted code cannot complete both.
