# Q3743: prepared state kept on the API instance in PhoneApi.ts

## Question
src/client/auth/PhoneApi.ts caches prepared state (wallet, prepared message) on the API object; can an attacker exploit a second init overwriting that state so a signature prepared for one address is submitted for another?

## Target
- File/function: [src/client/auth/PhoneApi.ts](src/client/auth/PhoneApi.ts) - PhoneApi.sendCode, loginWithCode, linkWithCode, updatePhone, unlink
- Entrypoint: privy.auth.phone.loginWithCode(phone, code)
- Attacker controls: phoneNumber string (unnormalized), code, mode, opts.embedded
- Exploit idea: Call init for address A, then init for address B, then complete the login with A's signature.
- Invariant to test: Prepared authentication state must be bound to the address it was created for.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run two init calls and assert the login rejects a signature that does not match the latest prepared address.
