# Q2372: off-chain domain truncated to 32 bytes in smart-wallets.ts

## Question
deriveSolanaApplicationDomain copies the first 32 UTF-8 bytes of the origin into the application domain; can an attacker register a longer origin that collides with the victim's origin after truncation so smart-wallets entry (BICONOMY produces messages the victim's verifier accepts?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Find two origins sharing a 32-byte prefix and compare derived domains.
- Invariant to test: The application domain must be collision-resistant over origins.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert two distinct origins never produce the same domain from smart-wallets entry (BICONOMY.
