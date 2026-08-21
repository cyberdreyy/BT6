# Q3535: oauth_tokens emitted to any listener in SmartWalletApi.ts

## Question
Provider tokens from SmartWalletApi.init are emitted through the session 'oauth_tokens_granted' event to every registered listener; can an attacker register or keep a listener that receives another flow's provider tokens?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Attach a listener, trigger an unrelated login flow, and observe the tokens delivered.
- Invariant to test: Provider tokens must only reach the flow that requested them.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: attach a listener, run an unrelated SmartWalletApi.init flow and assert the listener is not invoked.
