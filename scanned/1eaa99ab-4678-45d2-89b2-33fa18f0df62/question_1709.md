# Q1709: solana signer key taken from static keys only in client.ts

## Question
getWalletPublicKeyFromTransaction searches message.staticAccountKeys for the wallet address; can an attacker submit a versioned transaction that references the wallet through an address lookup table so SolanaClient.invokeRpc signs a transaction whose real account set is hidden?

## Target
- File/function: [src/solana/client.ts](src/solana/client.ts) - SolanaClient.invokeRpc, getBalance, getTokenAccountsByOwner, getAccountInfo (cluster.rpcUrl, errors swallowed to null)
- Entrypoint: new SolanaClient(cluster) balance/token reads used by funding UIs
- Attacker controls: cluster.rpcUrl value, RPC response shape, null-on-error results consumed as truth
- Exploit idea: Build a versioned transaction with the signer resolved via an ALT.
- Invariant to test: Signer resolution must account for the full resolved account list, not just static keys.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an ALT-using versioned transaction to SolanaClient.invokeRpc and assert it is rejected or fully resolved.
