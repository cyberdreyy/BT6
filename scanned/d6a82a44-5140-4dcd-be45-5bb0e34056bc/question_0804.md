# Q0804: clearMfa userId is caller supplied in MfaPasskeyApi.ts

## Question
clearMfa forwards the caller's userId to the iframe; can an attacker pass another user's id through MfaPasskeyApi.generateAuthenticationOptions to drop MFA state that is not theirs?

## Target
- File/function: [src/client/mfa/MfaPasskeyApi.ts](src/client/mfa/MfaPasskeyApi.ts) - MfaPasskeyApi.generateAuthenticationOptions
- Entrypoint: privy.mfa.passkey.generateAuthenticationOptions(input)
- Attacker controls: relying party and options fields echoed into the passkey ceremony
- Exploit idea: Call the clear path with a foreign user id.
- Invariant to test: MFA clearing must be scoped to the authenticated session's own user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call MfaPasskeyApi.generateAuthenticationOptions with a foreign userId and assert the session's own id is used or the call is refused.
