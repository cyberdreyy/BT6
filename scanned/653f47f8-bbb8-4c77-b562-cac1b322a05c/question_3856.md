# Q3856: uppercase or checksummed address mismatch in PasskeyApi.ts

## Question
Can an attacker exploit address case handling in PasskeyApi.generateAuthenticationOptions so the address used for the nonce request differs textually from the address embedded in the signed message?

## Target
- File/function: [src/client/auth/PasskeyApi.ts](src/client/auth/PasskeyApi.ts) - PasskeyApi.generateAuthenticationOptions, loginWithPasskey, signupWithPasskey, linkWithPasskey, _transformAuthenticationResponseToSnakeCase
- Entrypoint: privy.auth.passkey.loginWithPasskey(response, challenge, relyingParty)
- Attacker controls: relyingParty string, challenge, authenticator response object fields
- Exploit idea: Request the nonce with a lowercase address and sign a checksummed variant.
- Invariant to test: Address comparison in src/client/auth/PasskeyApi.ts must be canonical.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: feed mixed-case address pairs to PasskeyApi.generateAuthenticationOptions and assert consistent canonicalisation.
