# Q0800: clearMfa userId is caller supplied in withMfa.ts

## Question
clearMfa forwards the caller's userId to the iframe; can an attacker pass another user's id through withMfa retry loop (4 attempts to drop MFA state that is not theirs?

## Target
- File/function: [src/embedded/withMfa.ts](src/embedded/withMfa.ts) - withMfa retry loop (4 attempts, 300000ms per MFA wait, mfaAlwaysRequired flag)
- Entrypoint: every EmbeddedWalletProxy.invokeWithMfa operation
- Attacker controls: timing of mfa promise resolution, error types returned into the loop
- Exploit idea: Call the clear path with a foreign user id.
- Invariant to test: MFA clearing must be scoped to the authenticated session's own user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call withMfa retry loop (4 attempts with a foreign userId and assert the session's own id is used or the call is refused.
