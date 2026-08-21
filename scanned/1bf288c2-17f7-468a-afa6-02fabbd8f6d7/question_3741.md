# Q3741: prepared state kept on the API instance in AuthApi.ts

## Question
src/client/auth/AuthApi.ts caches prepared state (wallet, prepared message) on the API object; can an attacker exploit a second init overwriting that state so a signature prepared for one address is submitted for another?

## Target
- File/function: [src/client/auth/AuthApi.ts](src/client/auth/AuthApi.ts) - AuthApi.logout, AuthApi.email/phone/oauth/siwe/siws/passkey sub-APIs
- Entrypoint: privy.auth.logout(), privy.auth.<method>
- Attacker controls: logout timing, userId passed to mfa.clearMfa, concurrent login calls
- Exploit idea: Call init for address A, then init for address B, then complete the login with A's signature.
- Invariant to test: Prepared authentication state must be bound to the address it was created for.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: run two init calls and assert the login rejects a signature that does not match the latest prepared address.
