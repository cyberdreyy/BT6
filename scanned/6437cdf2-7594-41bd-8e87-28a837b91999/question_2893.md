# Q2893: verifyMfa reachable without a pending operation in MfaSmsApi.ts

## Question
MfaApi.verifyMfa can be invoked directly; can an attacker call MfaSmsApi.sendCode to consume an MFA code outside any operation, leaving a satisfied MFA state that a later operation reuses?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Call verifyMfa alone, then immediately start a signing operation.
- Invariant to test: An MFA verification must be consumed by the operation that required it.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: call MfaSmsApi.sendCode then a signature and assert the signature still requires its own MFA round.
