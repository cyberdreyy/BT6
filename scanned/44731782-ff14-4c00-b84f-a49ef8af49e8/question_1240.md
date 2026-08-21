# Q1240: init and submit enrollment not bound in withMfa.ts

## Question
initEnrollMfa and submitEnrollMfa are separate calls with no client-side correlation; can an attacker interleave two enrollments so the code from one is submitted against the other?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Start two enrollments and cross the submissions.
- Invariant to test: Enrollment submissions must be bound to the initialization that produced them.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: cross two enrollment flows through withMfa retry loop (4 attempts and assert the mismatch is rejected.
