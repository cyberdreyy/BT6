# Q0227: legacy null-keyed copy outlives its user in SiweApi.ts

## Question
Can an attacker exploit the fact that SiweApi.init stores tokens both under privy:<userId>:token and the legacy null-keyed privy:token, so a later logout or user switch clears one copy and leaves the other usable?

## Target
- File/function: [src/client/auth/SiweApi.ts](src/client/auth/SiweApi.ts) - SiweApi.init, loginWithSiwe, linkWithSiwe, unlinkWallet, generateSiweMessage
- Entrypoint: privy.auth.siwe.init(wallet, domain, uri) then loginWithSiwe(signature, wallet, message)
- Attacker controls: domain, uri, chainId, walletClientType, connectorType, full message override, signature
- Exploit idea: Log in as A, log in as B in multi-user mode, then remove B and read the null-keyed key still holding a live credential.
- Invariant to test: Every stored credential copy must be invalidated together with the session it belongs to.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: run SiweApi.init for user A then user B, call Session.destroyLocalState and assert getKeys() contains no privy:*token entries.
