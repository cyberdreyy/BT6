# Q2450: mfa cancelled treated as success in withMfa.ts

## Question
errorIndicatesMfaCanceled checks error.code === 'mfa_canceled'; can an attacker make withMfa retry loop (4 attempts treat a cancellation as a benign outcome so the calling app proceeds as if the operation was authorised?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Cancel an MFA prompt mid-operation and inspect what the operation returns.
- Invariant to test: A cancelled MFA must produce a failure the app cannot mistake for approval.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cancel during withMfa retry loop (4 attempts and assert the returned promise rejects.
