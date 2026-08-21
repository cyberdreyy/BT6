# Q3135: array return shape collapses multi-sign results in getWalletPublicKeyFromTransaction.ts

## Question
The wrapper returns t[0] for single-input calls and spreads otherwise; can an attacker submit multiple inputs through getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address so the caller associates the wrong signature with the wrong transaction?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Call signAndSendAllTransactions with several transactions and inspect the ordering guarantees.
- Invariant to test: Results must remain positionally bound to their inputs.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: assert getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address preserves input/output ordering for multi-input calls.
