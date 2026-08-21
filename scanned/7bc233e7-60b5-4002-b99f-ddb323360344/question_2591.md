# Q2591: off-chain parser trusts the preamble in unified-wallet.ts

## Question
parseSolanaOffchainMessage validates the 0xFF prefix and the 'solana offchain' text but returns version, format and signer bytes unchecked; can an attacker feed bytes through isUnifiedWallet (account.id && recovery_method === 'privy-v2') so the parsed signer public key differs from the actual signer?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Parse a crafted buffer with an arbitrary signer field.
- Invariant to test: Parsed signer identity must be verified against the expected signer.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: parse a crafted buffer through isUnifiedWallet (account.id && recovery_method === 'privy-v2') and assert the signer is validated.
