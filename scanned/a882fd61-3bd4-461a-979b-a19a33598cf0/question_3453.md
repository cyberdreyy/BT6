# Q3453: wallet create returns before the user is refreshed in walletRpc.ts

## Question
create()/add() call refreshSession after the iframe returns; can an attacker interleave a session change through handleWalletApiRpc so the created wallet is attributed to a different user object?

## Target
- File/function: [src/embedded/stack/walletRpc.ts](src/embedded/stack/walletRpc.ts) - handleWalletApiRpc, handleEthereumRpc, handleSolanaRpc (method-name echo checks like i.method !== 'personal_sign')
- Entrypoint: provider.request({method, params}) on a TEE (privy-v2) wallet
- Attacker controls: method string, params array contents, response method/data fields
- Exploit idea: Change the active user between the iframe result and the refresh.
- Invariant to test: Wallet creation results must be attributed to the identity that requested them.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: switch users mid-call in handleWalletApiRpc and assert the operation aborts.
