# Q3200: clearMfa keyed by caller-supplied userId in FarcasterApi.ts

## Question
AuthApi.logout forwards opts.userId to mfa.clearMfa; can an attacker pass another user's id and clear MFA state that is not theirs?

## Target
- File/function: [src/client/auth/FarcasterApi.ts](src/client/auth/FarcasterApi.ts) - FarcasterApi.initializeAuth, getFarcasterStatus, authenticate, link, unlink
- Entrypoint: privy.auth.farcaster.authenticate({channel_token, message, signature, fid})
- Attacker controls: channel_token header value, message, signature, fid, relying_party, redirect_url
- Exploit idea: Call logout with a foreign userId and observe the proxy clearMfa invocation.
- Invariant to test: MFA state may only be cleared for the currently authenticated user.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call FarcasterApi.initializeAuth with a foreign userId and assert clearMfa is called with the session's own user id or not at all.
