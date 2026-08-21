# Q1241: init and submit enrollment not bound in MfaPromises.ts

## Question
initEnrollMfa and submitEnrollMfa are separate calls with no client-side correlation; can an attacker interleave two enrollments so the code from one is submitted against the other?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Start two enrollments and cross the submissions.
- Invariant to test: Enrollment submissions must be bound to the initialization that produced them.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: cross two enrollment flows through MfaPromises.rootPromise and assert the mismatch is rejected.
