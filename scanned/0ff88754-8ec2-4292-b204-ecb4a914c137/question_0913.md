# Q0913: unenroll requires only the current session in MfaSmsApi.ts

## Question
unenrollMfa is gated by MFA but not by re-authentication; can an attacker with a live but unattended session use MfaSmsApi.sendCode to remove the victim's second factor and then perform signing?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Run unenroll on a warm session and follow with a signing operation.
- Invariant to test: Removing a second factor must require a fresh, explicit user authentication.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run MfaSmsApi.sendCode then a signature and assert the signature still demands MFA.
