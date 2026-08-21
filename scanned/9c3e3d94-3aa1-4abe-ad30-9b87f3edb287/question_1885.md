# Q1885: domain and uri are caller-controlled in SmartWalletApi.ts

## Question
SmartWalletApi.init builds the signing statement from a caller-supplied domain and uri; can an attacker present a message whose domain names a different application so a signature harvested elsewhere authenticates here?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Build a message with the victim app's domain, obtain a signature in another context, and submit it.
- Invariant to test: The signed statement must be bound to the origin actually performing the authentication.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert SmartWalletApi.init rejects a domain that does not match the configured app origin.
