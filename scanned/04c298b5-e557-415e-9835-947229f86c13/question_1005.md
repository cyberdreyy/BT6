# Q1005: concurrent login writes interleave active-user pointer in SmartWalletApi.ts

## Question
Can an unprivileged attacker race two privy.auth.smartWallet.init(wallet) then link(message, signature, type, version) calls so storeActiveUserId writes user B while the later-resolving login stores user A's tokens under the null key, making privy:active-user point at the wrong credentials?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Fire both logins, delay one response, then read getActiveUserId and getCustomerAccessToken.
- Invariant to test: privy:active-user and the null-keyed token copy must always describe the same subject.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: interleave two SmartWalletApi.init promises with controlled resolution order and assert Token.parse(getCustomerAccessToken()).subject === getActiveUserId().
