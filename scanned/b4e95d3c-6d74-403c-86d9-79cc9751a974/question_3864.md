# Q3864: uppercase or checksummed address mismatch in GuestApi.ts

## Question
Can an attacker exploit address case handling in GuestApi.create so the address used for the nonce request differs textually from the address embedded in the signed message?

## Target
- File/function: [src/client/auth/GuestApi.ts](src/client/auth/GuestApi.ts) - GuestApi.create, session.getOrCreateGuestCredential (privy:guest:<appId>)
- Entrypoint: privy.auth.guest.create()
- Attacker controls: guest credential value persisted in localStorage, repeated create calls
- Exploit idea: Request the nonce with a lowercase address and sign a checksummed variant.
- Invariant to test: Address comparison in src/client/auth/GuestApi.ts must be canonical.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: feed mixed-case address pairs to GuestApi.create and assert consistent canonicalisation.
