# Q1335: 30 day refresh cookie on a shared browser in SmartWalletApi.ts

## Question
Session.storeRefreshTokenForUser sets a 30-day refresh cookie; after SmartWalletApi.init, can a later unprivileged user of the same browser profile resume the previous account because logout only clears the active user's keys?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Log in, close the tab without logout, then in a new SDK instance call refreshSession and observe a restored session.
- Invariant to test: A session must not be resumable after the SDK's own clear paths have run for that user.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: run SmartWalletApi.init, call destroyLocalState, construct a fresh Privy client and assert refreshSession throws MISSING_OR_INVALID_TOKEN.
