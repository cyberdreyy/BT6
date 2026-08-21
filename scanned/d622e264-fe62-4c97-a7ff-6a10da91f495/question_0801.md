# Q0801: clearMfa userId is caller supplied in MfaPromises.ts

## Question
clearMfa forwards the caller's userId to the iframe; can an attacker pass another user's id through MfaPromises.rootPromise to drop MFA state that is not theirs?

## Target
- File/function: [src/client/MfaPromises.ts](src/client/MfaPromises.ts) - MfaPromises.rootPromise, submitPromise, 'mfaRequired' event
- Entrypoint: privy.mfaPromises listeners in the integrating app
- Attacker controls: who resolves/rejects the shared promise refs, ordering of concurrent operations
- Exploit idea: Call the clear path with a foreign user id.
- Invariant to test: MFA clearing must be scoped to the authenticated session's own user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call MfaPromises.rootPromise with a foreign userId and assert the session's own id is used or the call is refused.
