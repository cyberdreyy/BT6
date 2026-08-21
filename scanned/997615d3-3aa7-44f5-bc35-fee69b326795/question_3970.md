# Q3970: no expiry in the signed statement in FarcasterApi.ts

## Question
The statement built in src/client/auth/FarcasterApi.ts carries Issued At but no expiration; can an attacker replay a signature captured months earlier through FarcasterApi.initializeAuth?

## Target
- File/function: [src/client/auth/FarcasterApi.ts](src/client/auth/FarcasterApi.ts) - FarcasterApi.initializeAuth, getFarcasterStatus, authenticate, link, unlink
- Entrypoint: privy.auth.farcaster.authenticate({channel_token, message, signature, fid})
- Attacker controls: channel_token header value, message, signature, fid, relying_party, redirect_url
- Exploit idea: Sign once, store the message and signature, replay after a long delay.
- Invariant to test: Authentication statements must carry an expiry the client enforces.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert FarcasterApi.initializeAuth rejects a message whose Issued At is older than a short window.
