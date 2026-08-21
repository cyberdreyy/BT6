# Q0565: wallet auto-creation runs before user is confirmed in SmartWalletApi.ts

## Question
maybeCreateWalletOnLogin runs immediately after SmartWalletApi.init stores tokens; can an attacker interleave a second login so the wallet created by createOnLogin is provisioned under the wrong active user id?

## Target
- File/function: [src/client/auth/SmartWalletApi.ts](src/client/auth/SmartWalletApi.ts) - SmartWalletApi.init, link, generateSmartWalletSiweMessage
- Entrypoint: privy.auth.smartWallet.init(wallet) then link(message, signature, type, version)
- Attacker controls: message override, signature, smart_wallet_type, smart_wallet_version, chainId
- Exploit idea: Start two logins for different accounts, let the first reach maybeCreateWalletOnLogin while the second updates privy:active-user.
- Invariant to test: A wallet created on login must be created for exactly the user whose tokens that login stored.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: run two SmartWalletApi.init calls concurrently with distinct users and assert each created wallet's owner matches its own login response.
