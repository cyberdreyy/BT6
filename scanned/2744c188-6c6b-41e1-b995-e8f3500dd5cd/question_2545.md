# Q2545: third-party auth blob forwarded verbatim in SmartWalletApi.ts

## Question
SmartWalletApi.init forwards the provider payload (web app data, auth result, token, or channel token) verbatim; can an attacker craft a payload whose embedded identity fields disagree with each other so the client-side flow proceeds on the wrong identity?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Assemble a payload with inconsistent identity fields and observe that the SDK performs no cross-field check before storing whatever session comes back.
- Invariant to test: src/client/auth/SmartWalletApi.ts must not treat an unvalidated provider payload as an identity assertion for its own session bookkeeping.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: submit a payload with mismatched identity fields and assert SmartWalletApi.init does not call updateWithTokensResponse without server confirmation of the same subject.
