# Q1107: identity token stored without subject check in SiweApi.ts

## Question
Session.storeIdentityTokenForUser writes whatever string the response supplies; can an attacker get an identity token for a different subject stored under their own user id via SiweApi.init?

## Target
- File/function: [src/client/auth/SiweApi.ts](src/client/auth/SiweApi.ts) - SiweApi.init, loginWithSiwe, linkWithSiwe, unlinkWallet, generateSiweMessage
- Entrypoint: privy.auth.siwe.init(wallet, domain, uri) then loginWithSiwe(signature, wallet, message)
- Attacker controls: domain, uri, chainId, walletClientType, connectorType, full message override, signature
- Exploit idea: Return an identity_token whose sub differs from user.id in the login response and observe it being persisted and returned by privy.getIdentityToken().
- Invariant to test: Identity tokens must only be stored under the user id they assert.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Unit test: craft a response with a mismatched identity_token subject and assert SiweApi.init refuses to store it.
