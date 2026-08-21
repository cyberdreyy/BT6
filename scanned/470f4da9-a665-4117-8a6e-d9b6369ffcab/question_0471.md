# Q0471: shared MfaPromises across operations in MfaPromises.ts

## Question
MfaPromises.rootPromise/submitPromise are single mutable refs shared by every operation; can an attacker start two operations so the MFA answer supplied for the low-value one satisfies the high-value one?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Start a benign operation and a signing operation, then resolve the submit promise once.
- Invariant to test: An MFA response must satisfy only the operation that requested it.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: start two MfaPromises.rootPromise-routed operations and assert one submitted code cannot complete both.
