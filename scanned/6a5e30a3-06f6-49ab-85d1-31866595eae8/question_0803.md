# Q0803: clearMfa userId is caller supplied in MfaSmsApi.ts

## Question
clearMfa forwards the caller's userId to the iframe; can an attacker pass another user's id through MfaSmsApi.sendCode to drop MFA state that is not theirs?

## Target
- File/function: [src/client/mfa/MfaSmsApi.ts](src/client/mfa/MfaSmsApi.ts) - MfaSmsApi.sendCode
- Entrypoint: privy.mfa.sms.sendCode(input)
- Attacker controls: phone/target fields in the input body, repetition
- Exploit idea: Call the clear path with a foreign user id.
- Invariant to test: MFA clearing must be scoped to the authenticated session's own user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call MfaSmsApi.sendCode with a foreign userId and assert the session's own id is used or the call is refused.
