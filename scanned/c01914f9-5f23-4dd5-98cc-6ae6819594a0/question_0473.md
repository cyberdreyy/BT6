# Q0473: shared MfaPromises across operations in MfaSmsApi.ts

## Question
MfaPromises.rootPromise/submitPromise are single mutable refs shared by every operation; can an attacker start two operations so the MFA answer supplied for the low-value one satisfies the high-value one?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Start a benign operation and a signing operation, then resolve the submit promise once.
- Invariant to test: An MFA response must satisfy only the operation that requested it.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: start two MfaSmsApi.sendCode-routed operations and assert one submitted code cannot complete both.
