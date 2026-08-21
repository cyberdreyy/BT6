# Q0557: wallet auto-creation runs before user is confirmed in SiweApi.ts

## Question
maybeCreateWalletOnLogin runs immediately after SiweApi.init stores tokens; can an attacker interleave a second login so the wallet created by createOnLogin is provisioned under the wrong active user id?

## Target
- File/function: [src/client/auth/SiweApi.ts](src/client/auth/SiweApi.ts) - SiweApi.init, loginWithSiwe, linkWithSiwe, unlinkWallet, generateSiweMessage
- Entrypoint: privy.auth.siwe.init(wallet, domain, uri) then loginWithSiwe(signature, wallet, message)
- Attacker controls: domain, uri, chainId, walletClientType, connectorType, full message override, signature
- Exploit idea: Start two logins for different accounts, let the first reach maybeCreateWalletOnLogin while the second updates privy:active-user.
- Invariant to test: A wallet created on login must be created for exactly the user whose tokens that login stored.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run two SiweApi.init calls concurrently with distinct users and assert each created wallet's owner matches its own login response.
