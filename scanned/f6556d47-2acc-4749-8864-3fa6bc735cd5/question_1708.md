# Q1708: solana signer key taken from static keys only in ConnectedStandardSolanaWallet.ts

## Question
getWalletPublicKeyFromTransaction searches message.staticAccountKeys for the wallet address; can an attacker submit a versioned transaction that references the wallet through an address lookup table so ConnectedStandardSolanaWallet.signMessage signs a transaction whose real account set is hidden?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Build a versioned transaction with the signer resolved via an ALT.
- Invariant to test: Signer resolution must account for the full resolved account list, not just static keys.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an ALT-using versioned transaction to ConnectedStandardSolanaWallet.signMessage and assert it is rejected or fully resolved.
