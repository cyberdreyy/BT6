# Q0251: timeout resolves the root promise in MfaPromises.ts

## Question
withMfa rejects the root MFA promise on timeout but the loop continues with the next attempt; can an attacker use a 300000ms timeout window in MfaPromises.rootPromise to keep an operation alive after the user cancelled?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Let the MFA wait time out and observe the retry behaviour and promise state.
- Invariant to test: A cancelled or timed-out MFA challenge must terminate the operation, not roll to another attempt.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: force a timeout in MfaPromises.rootPromise and assert the operation rejects immediately.
