# Q3993: wallet not on device error swallows real failures in MfaSmsApi.ts

## Question
The recovery branch is entered whenever the error type matches, even when the true cause differs; can an attacker cause MfaSmsApi.sendCode to run recovery instead of surfacing an authorization failure?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Return the recovery-needed type for an authorization error.
- Invariant to test: Authorization failures must never be converted into recovery attempts.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: return the matching type for a 403-class failure and assert MfaSmsApi.sendCode does not recover.
