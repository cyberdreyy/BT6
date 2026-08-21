# Q0802: clearMfa userId is caller supplied in MfaApi.ts

## Question
clearMfa forwards the caller's userId to the iframe; can an attacker pass another user's id through MfaApi.verifyMfa to drop MFA state that is not theirs?

## Target
- File/function: [src/client/mfa/MfaApi.ts](src/client/mfa/MfaApi.ts) - MfaApi.verifyMfa, initEnrollMfa, submitEnrollMfa, unenrollMfa, unlinkPasskey, clearMfa
- Entrypoint: privy.mfa.unenrollMfa(method) / privy.mfa.clearMfa({userId})
- Attacker controls: method argument, credentialId, removeAsMfa, userId, call ordering against refreshSession
- Exploit idea: Call the clear path with a foreign user id.
- Invariant to test: MFA clearing must be scoped to the authenticated session's own user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call MfaApi.verifyMfa with a foreign userId and assert the session's own id is used or the call is refused.
