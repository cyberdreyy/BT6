# Q2215: relying party string controlled by caller in SmartWalletApi.ts

## Question
In src/client/auth/SmartWalletApi.ts, is the relying party supplied by the caller and echoed into the ceremony, letting an attacker start a credential ceremony scoped to a different origin than the one they occupy?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Call SmartWalletApi.init with a relying party that is not the current origin and observe the options returned.
- Invariant to test: The relying party used by SmartWalletApi.init must be derived from the app's configured origin.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call SmartWalletApi.init with a foreign relying party and assert the SDK refuses.
