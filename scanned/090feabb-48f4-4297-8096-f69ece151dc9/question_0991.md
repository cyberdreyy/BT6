# Q0991: concurrent login writes interleave active-user pointer in AuthApi.ts

## Question
Can an unprivileged attacker race two privy.auth.logout(), privy.auth.<method> calls so storeActiveUserId writes user B while the later-resolving login stores user A's tokens under the null key, making privy:active-user point at the wrong credentials?

## Target
- File/function: [src/client/auth/AuthApi.ts](src/client/auth/AuthApi.ts) - AuthApi.logout, AuthApi.email/phone/oauth/siwe/siws/passkey sub-APIs
- Entrypoint: privy.auth.logout(), privy.auth.<method>
- Attacker controls: logout timing, userId passed to mfa.clearMfa, concurrent login calls
- Exploit idea: Fire both logins, delay one response, then read getActiveUserId and getCustomerAccessToken.
- Invariant to test: privy:active-user and the null-keyed token copy must always describe the same subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: interleave two AuthApi.logout promises with controlled resolution order and assert Token.parse(getCustomerAccessToken()).subject === getActiveUserId().
