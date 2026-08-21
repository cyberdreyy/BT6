# Q1555: code_verifier survives a failed exchange in SmartWalletApi.ts

## Question
Does SmartWalletApi.init leave privy:code_verifier and privy:state_code in storage when the exchange throws, so a later attacker-triggered callback can replay them?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Fail the authenticate request, then deliver a crafted callback that reuses the still-stored state/verifier pair.
- Invariant to test: PKCE material must be deleted on every terminal outcome, not only on success.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: make the exchange reject and assert both storage keys are absent afterwards.
