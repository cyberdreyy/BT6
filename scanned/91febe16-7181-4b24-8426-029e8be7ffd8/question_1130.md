# Q1130: enrollment submitted for a different method in withMfa.ts

## Question
submitEnrollMfa branches on method === 'passkey' for the MFA-gated path and takes the other branch otherwise; can an attacker choose the ungated branch to enrol a method without an MFA challenge?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Call the submit path with a non-passkey method and observe the gate.
- Invariant to test: All enrollment submissions must pass the same gate in src/embedded/withMfa.ts.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: submit each method through withMfa retry loop (4 attempts and assert every path is MFA-gated.
