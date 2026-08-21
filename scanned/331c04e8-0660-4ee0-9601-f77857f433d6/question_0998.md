# Q0998: concurrent login writes interleave active-user pointer in SiwsApi.ts

## Question
Can an unprivileged attacker race two privy.auth.siws.login({message, signature, walletClientType, connectorType, mode}) calls so storeActiveUserId writes user B while the later-resolving login stores user A's tokens under the null key, making privy:active-user point at the wrong credentials?

## Target
- File/function: [src/client/auth/SiwsApi.ts](src/client/auth/SiwsApi.ts) - SiwsApi.fetchNonce, login, link, unlink
- Entrypoint: privy.auth.siws.login({message, signature, walletClientType, connectorType, mode})
- Attacker controls: message string, signature, wallet metadata, nonce reuse
- Exploit idea: Fire both logins, delay one response, then read getActiveUserId and getCustomerAccessToken.
- Invariant to test: privy:active-user and the null-keyed token copy must always describe the same subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: interleave two SiwsApi.fetchNonce promises with controlled resolution order and assert Token.parse(getCustomerAccessToken()).subject === getActiveUserId().
