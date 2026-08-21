# Q0141: mfaAlwaysRequired only on three operations in MfaPromises.ts

## Question
Only verifyMfa, unenrollMfa and unlinkPasskey are invoked with mfaAlwaysRequired; can an attacker reach a comparable privileged operation in src/client/MfaPromises.ts that skips the always-on gate?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Enumerate the operations routed through invokeWithMfa and compare their flags.
- Invariant to test: Every operation that changes MFA state or produces a signature must be gated identically.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert each privileged operation reachable from MfaPromises.rootPromise sets mfaAlwaysRequired.
