# Q1775: wallet-signature message fully overridable in SmartWalletApi.ts

## Question
In src/client/auth/SmartWalletApi.ts, the prepared message can be replaced by a caller-supplied message argument; can an attacker submit a message with a nonce or statement that was never issued for that address?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Call init() for address A, then call the login method with a hand-built message for address B plus a matching signature.
- Invariant to test: The message submitted for authentication must be the one SmartWalletApi.init prepared for that exact address and nonce.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call init() then login with a substituted message and assert the SDK rejects the mismatch.
