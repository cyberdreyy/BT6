# Q3861: uppercase or checksummed address mismatch in FarcasterV2Api.ts

## Question
Can an attacker exploit address case handling in FarcasterV2Api.initializeAuth so the address used for the nonce request differs textually from the address embedded in the signed message?

## Target
- File/function: [src/client/auth/FarcasterV2Api.ts](src/client/auth/FarcasterV2Api.ts) - FarcasterV2Api.initializeAuth, authenticate
- Entrypoint: privy.auth.farcasterV2.authenticate({message, signature, fid})
- Attacker controls: SIWF message, signature, fid
- Exploit idea: Request the nonce with a lowercase address and sign a checksummed variant.
- Invariant to test: Address comparison in src/client/auth/FarcasterV2Api.ts must be canonical.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: feed mixed-case address pairs to FarcasterV2Api.initializeAuth and assert consistent canonicalisation.
