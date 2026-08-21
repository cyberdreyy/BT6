# Q3191: clearMfa keyed by caller-supplied userId in AuthApi.ts

## Question
AuthApi.logout forwards opts.userId to mfa.clearMfa; can an attacker pass another user's id and clear MFA state that is not theirs?

## Target
- File/function: [src/client/auth/AuthApi.ts](src/client/auth/AuthApi.ts) - AuthApi.logout, AuthApi.email/phone/oauth/siwe/siws/passkey sub-APIs
- Entrypoint: privy.auth.logout(), privy.auth.<method>
- Attacker controls: logout timing, userId passed to mfa.clearMfa, concurrent login calls
- Exploit idea: Call logout with a foreign userId and observe the proxy clearMfa invocation.
- Invariant to test: MFA state may only be cleared for the currently authenticated user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call AuthApi.logout with a foreign userId and assert clearMfa is called with the session's own user id or not at all.
