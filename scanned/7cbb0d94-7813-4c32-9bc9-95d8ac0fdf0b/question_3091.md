# Q3091: logout does not await server revocation in FarcasterV2Api.ts

## Question
AuthApi.logout swallows the Logout request error before clearing local state; can an attacker abuse this so the refresh token stays valid server-side while the app reports a completed logout?

## Target
- File/function: [src/client/auth/FarcasterV2Api.ts](src/client/auth/FarcasterV2Api.ts) - FarcasterV2Api.initializeAuth, authenticate
- Entrypoint: privy.auth.farcasterV2.authenticate({message, signature, fid})
- Attacker controls: SIWF message, signature, fid
- Exploit idea: Make the Logout route fail and then reuse the previously captured refresh token.
- Invariant to test: A completed logout must guarantee server-side revocation or surface the failure.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: fail the Logout route, assert FarcasterV2Api.initializeAuth surfaces the failure instead of resolving silently.
