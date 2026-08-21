# Q2371: off-chain domain truncated to 32 bytes in unified-wallet.ts

## Question
deriveSolanaApplicationDomain copies the first 32 UTF-8 bytes of the origin into the application domain; can an attacker register a longer origin that collides with the victim's origin after truncation so isUnifiedWallet (account.id && recovery_method === 'privy-v2') produces messages the victim's verifier accepts?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Find two origins sharing a 32-byte prefix and compare derived domains.
- Invariant to test: The application domain must be collision-resistant over origins.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert two distinct origins never produce the same domain from isUnifiedWallet (account.id && recovery_method === 'privy-v2').
