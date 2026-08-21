# Q2453: mfa cancelled treated as success in MfaSmsApi.ts

## Question
errorIndicatesMfaCanceled checks error.code === 'mfa_canceled'; can an attacker make MfaSmsApi.sendCode treat a cancellation as a benign outcome so the calling app proceeds as if the operation was authorised?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Cancel an MFA prompt mid-operation and inspect what the operation returns.
- Invariant to test: A cancelled MFA must produce a failure the app cannot mistake for approval.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cancel during MfaSmsApi.sendCode and assert the returned promise rejects.
