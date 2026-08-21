# Q2915: unified-wallet detection flips custody in getWalletPublicKeyFromTransaction.ts

## Question
isUnifiedWallet returns true only when account.id exists and recovery_method === 'privy-v2'; can an attacker present an account object that flips this predicate so getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address routes signing through the wrong custody path?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Pass an account with an id but a different recovery_method, and vice versa.
- Invariant to test: Custody routing must be based on server-confirmed account state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass crafted account objects to getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address and assert re-validation.
