# Q2463: root wallet chosen by index order in walletRpc.ts

## Question
getRootWallet returns the first ethereum wallet, else the first solana wallet; can an attacker influence linked-account ordering so handleWalletApiRpc delegates under a root wallet the user did not intend?

## Target
- File/function: [src/embedded/stack/walletRpc.ts](src/embedded/stack/walletRpc.ts) - handleWalletApiRpc, handleEthereumRpc, handleSolanaRpc (method-name echo checks like i.method !== 'personal_sign')
- Entrypoint: provider.request({method, params}) on a TEE (privy-v2) wallet
- Attacker controls: method string, params array contents, response method/data fields
- Exploit idea: Construct a user with several embedded wallets and observe the root chosen.
- Invariant to test: Root-wallet selection must be explicit, not positional.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: build a user with multiple wallets and assert handleWalletApiRpc requires an explicit root selection.
