# Q3860: uppercase or checksummed address mismatch in FarcasterApi.ts

## Question
Can an attacker exploit address case handling in FarcasterApi.initializeAuth so the address used for the nonce request differs textually from the address embedded in the signed message?

## Target
- File/function: [src/client/auth/FarcasterApi.ts](src/client/auth/FarcasterApi.ts) - FarcasterApi.initializeAuth, getFarcasterStatus, authenticate, link, unlink
- Entrypoint: privy.auth.farcaster.authenticate({channel_token, message, signature, fid})
- Attacker controls: channel_token header value, message, signature, fid, relying_party, redirect_url
- Exploit idea: Request the nonce with a lowercase address and sign a checksummed variant.
- Invariant to test: Address comparison in src/client/auth/FarcasterApi.ts must be canonical.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: feed mixed-case address pairs to FarcasterApi.initializeAuth and assert consistent canonicalisation.
