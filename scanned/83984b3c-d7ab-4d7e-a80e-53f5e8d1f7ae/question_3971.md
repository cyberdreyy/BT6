# Q3971: no expiry in the signed statement in FarcasterV2Api.ts

## Question
The statement built in src/client/auth/FarcasterV2Api.ts carries Issued At but no expiration; can an attacker replay a signature captured months earlier through FarcasterV2Api.initializeAuth?

## Target
- File/function: [src/client/auth/FarcasterV2Api.ts](src/client/auth/FarcasterV2Api.ts) - FarcasterV2Api.initializeAuth, authenticate
- Entrypoint: privy.auth.farcasterV2.authenticate({message, signature, fid})
- Attacker controls: SIWF message, signature, fid
- Exploit idea: Sign once, store the message and signature, replay after a long delay.
- Invariant to test: Authentication statements must carry an expiry the client enforces.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert FarcasterV2Api.initializeAuth rejects a message whose Issued At is older than a short window.
