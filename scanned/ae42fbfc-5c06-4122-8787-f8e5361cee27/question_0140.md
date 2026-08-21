# Q0140: mfaAlwaysRequired only on three operations in withMfa.ts

## Question
Only verifyMfa, unenrollMfa and unlinkPasskey are invoked with mfaAlwaysRequired; can an attacker reach a comparable privileged operation in src/embedded/withMfa.ts that skips the always-on gate?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Enumerate the operations routed through invokeWithMfa and compare their flags.
- Invariant to test: Every operation that changes MFA state or produces a signature must be gated identically.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: assert each privileged operation reachable from withMfa retry loop (4 attempts sets mfaAlwaysRequired.
