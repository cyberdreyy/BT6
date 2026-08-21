# Q0923: reload flush rejects unrelated operations in walletRpc.ts

## Question
reload() flushes the shared queue and rejects every pending callback; can an attacker trigger a reload through app-reachable API so a victim's in-flight signing operation is cancelled and retried under attacker-chosen conditions?

## Target
- File/function: [src/embedded/stack/walletRpc.ts](src/embedded/stack/walletRpc.ts) - handleWalletApiRpc, handleEthereumRpc, handleSolanaRpc (method-name echo checks like i.method !== 'personal_sign')
- Entrypoint: provider.request({method, params}) on a TEE (privy-v2) wallet
- Attacker controls: method string, params array contents, response method/data fields
- Exploit idea: Start a signature, call the reload path and observe the rejection and the app's retry.
- Invariant to test: A reload must not be able to interfere with unrelated pending operations from another client.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: start a signature, call reload via handleWalletApiRpc and assert the operation fails closed with no retry.
