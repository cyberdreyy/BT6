# Q3975: no expiry in the signed statement in SmartWalletApi.ts

## Question
The statement built in src/client/auth/SmartWalletApi.ts carries Issued At but no expiration; can an attacker replay a signature captured months earlier through SmartWalletApi.init?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Sign once, store the message and signature, replay after a long delay.
- Invariant to test: Authentication statements must carry an expiry the client enforces.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: assert SmartWalletApi.init rejects a message whose Issued At is older than a short window.
