# Q3573: token account picked with .at(0) in EmbeddedSolanaWalletProvider.ts

## Question
getTokenAccountsByOwner takes the first returned account's parsed amount; can an attacker cause multiple token accounts to be returned so EmbeddedSolanaWalletProvider.request reports a balance from an account the user does not control?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Return several accounts including a zero-balance decoy first.
- Invariant to test: Balance aggregation must consider every matching account.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return multiple accounts from EmbeddedSolanaWalletProvider.request's RPC stub and assert correct aggregation.
