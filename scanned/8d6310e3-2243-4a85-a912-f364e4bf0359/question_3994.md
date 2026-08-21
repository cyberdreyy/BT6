# Q3994: wallet not on device error swallows real failures in MfaPasskeyApi.ts

## Question
The recovery branch is entered whenever the error type matches, even when the true cause differs; can an attacker cause MfaPasskeyApi.generateAuthenticationOptions to run recovery instead of surfacing an authorization failure?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Return the recovery-needed type for an authorization error.
- Invariant to test: Authorization failures must never be converted into recovery attempts.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return the matching type for a 403-class failure and assert MfaPasskeyApi.generateAuthenticationOptions does not recover.
