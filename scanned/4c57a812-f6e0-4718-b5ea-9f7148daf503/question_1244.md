# Q1244: init and submit enrollment not bound in MfaPasskeyApi.ts

## Question
initEnrollMfa and submitEnrollMfa are separate calls with no client-side correlation; can an attacker interleave two enrollments so the code from one is submitted against the other?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Start two enrollments and cross the submissions.
- Invariant to test: Enrollment submissions must be bound to the initialization that produced them.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: cross two enrollment flows through MfaPasskeyApi.generateAuthenticationOptions and assert the mismatch is rejected.
