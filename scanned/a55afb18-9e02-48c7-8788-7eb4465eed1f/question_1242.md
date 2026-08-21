# Q1242: init and submit enrollment not bound in MfaApi.ts

## Question
initEnrollMfa and submitEnrollMfa are separate calls with no client-side correlation; can an attacker interleave two enrollments so the code from one is submitted against the other?

## Target
- File/function: [src/client/mfa/MfaApi.ts](src/client/mfa/MfaApi.ts) - MfaApi.verifyMfa, initEnrollMfa, submitEnrollMfa, unenrollMfa, unlinkPasskey, clearMfa
- Entrypoint: privy.mfa.unenrollMfa(method) / privy.mfa.clearMfa({userId})
- Attacker controls: method argument, credentialId, removeAsMfa, userId, call ordering against refreshSession
- Exploit idea: Start two enrollments and cross the submissions.
- Invariant to test: Enrollment submissions must be bound to the initialization that produced them.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: cross two enrollment flows through MfaApi.verifyMfa and assert the mismatch is rejected.
