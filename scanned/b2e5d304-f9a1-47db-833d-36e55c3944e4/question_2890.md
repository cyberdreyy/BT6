# Q2890: verifyMfa reachable without a pending operation in withMfa.ts

## Question
MfaApi.verifyMfa can be invoked directly; can an attacker call withMfa retry loop (4 attempts to consume an MFA code outside any operation, leaving a satisfied MFA state that a later operation reuses?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Call verifyMfa alone, then immediately start a signing operation.
- Invariant to test: An MFA verification must be consumed by the operation that required it.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: call withMfa retry loop (4 attempts then a signature and assert the signature still requires its own MFA round.
