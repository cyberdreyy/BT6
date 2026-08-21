# Q3201: clearMfa keyed by caller-supplied userId in FarcasterV2Api.ts

## Question
AuthApi.logout forwards opts.userId to mfa.clearMfa; can an attacker pass another user's id and clear MFA state that is not theirs?

## Target
- File/function: [src/client/auth/FarcasterV2Api.ts](src/client/auth/FarcasterV2Api.ts) - FarcasterV2Api.initializeAuth, authenticate
- Entrypoint: privy.auth.farcasterV2.authenticate({message, signature, fid})
- Attacker controls: SIWF message, signature, fid
- Exploit idea: Call logout with a foreign userId and observe the proxy clearMfa invocation.
- Invariant to test: MFA state may only be cleared for the currently authenticated user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call FarcasterV2Api.initializeAuth with a foreign userId and assert clearMfa is called with the session's own user id or not at all.
