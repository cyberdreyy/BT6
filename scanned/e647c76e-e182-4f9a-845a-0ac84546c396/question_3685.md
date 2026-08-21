# Q3685: chain id switch emits an event apps trust in getWalletPublicKeyFromTransaction.ts

## Question
internalSwitchEthereumChain emits chainChanged after mutating internal state; can an attacker force a switch through getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address so the app's UI shows one chain while signing occurs on another?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Trigger a switch during a pending signature and compare the UI chain to the signed chainId.
- Invariant to test: The chain displayed and the chain signed must be identical for every signature.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: interleave a switch with a signature through getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address and assert consistency.
