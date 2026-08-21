# Q1767: wallet-signature message fully overridable in SiweApi.ts

## Question
In src/client/auth/SiweApi.ts, the prepared message can be replaced by a caller-supplied message argument; can an attacker submit a message with a nonce or statement that was never issued for that address?

## Target
- File/function: [src/client/auth/SiweApi.ts](src/client/auth/SiweApi.ts) - SiweApi.init, loginWithSiwe, linkWithSiwe, unlinkWallet, generateSiweMessage
- Entrypoint: privy.auth.siwe.init(wallet, domain, uri) then loginWithSiwe(signature, wallet, message)
- Attacker controls: domain, uri, chainId, walletClientType, connectorType, full message override, signature
- Exploit idea: Call init() for address A, then call the login method with a hand-built message for address B plus a matching signature.
- Invariant to test: The message submitted for authentication must be the one SiweApi.init prepared for that exact address and nonce.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Unit test: call init() then login with a substituted message and assert the SDK rejects the mismatch.
