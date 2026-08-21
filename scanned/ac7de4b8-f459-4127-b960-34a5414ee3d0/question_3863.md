# Q3863: uppercase or checksummed address mismatch in CustomProviderApi.ts

## Question
Can an attacker exploit address case handling in CustomProviderApi.syncWithToken so the address used for the nonce request differs textually from the address embedded in the signed message?

## Target
- File/function: [src/client/auth/CustomProviderApi.ts](src/client/auth/CustomProviderApi.ts) - CustomProviderApi.syncWithToken, linkWithToken
- Entrypoint: privy.auth.customProvider.syncWithToken(token, opts, mode)
- Attacker controls: the third-party JWT string, mode, opts.embedded
- Exploit idea: Request the nonce with a lowercase address and sign a checksummed variant.
- Invariant to test: Address comparison in src/client/auth/CustomProviderApi.ts must be canonical.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: feed mixed-case address pairs to CustomProviderApi.syncWithToken and assert consistent canonicalisation.
