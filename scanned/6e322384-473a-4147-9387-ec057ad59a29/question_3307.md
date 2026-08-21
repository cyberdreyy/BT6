# Q3307: analytics event carries auth material in SiweApi.ts

## Question
createAnalyticsEvent payloads from src/client/auth/SiweApi.ts include flow details such as stored and returned state codes; can an attacker cause secret-bearing values to be shipped to the analytics route?

## Target
- File/function: [src/client/auth/SiweApi.ts](src/client/auth/SiweApi.ts) - SiweApi.init, loginWithSiwe, linkWithSiwe, unlinkWallet, generateSiweMessage
- Entrypoint: privy.auth.siwe.init(wallet, domain, uri) then loginWithSiwe(signature, wallet, message)
- Attacker controls: domain, uri, chainId, walletClientType, connectorType, full message override, signature
- Exploit idea: Trigger the mismatch path and inspect the analytics body.
- Invariant to test: No authentication secret may appear in an analytics payload emitted from src/client/auth/SiweApi.ts.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: trigger the failure path in SiweApi.init and assert the analytics body contains no verifier or token material.
