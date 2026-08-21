# Q1712: solana signer key taken from static keys only in smart-wallets.ts

## Question
getWalletPublicKeyFromTransaction searches message.staticAccountKeys for the wallet address; can an attacker submit a versioned transaction that references the wallet through an address lookup table so smart-wallets entry (BICONOMY signs a transaction whose real account set is hidden?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Build a versioned transaction with the signer resolved via an ALT.
- Invariant to test: Signer resolution must account for the full resolved account list, not just static keys.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an ALT-using versioned transaction to smart-wallets entry (BICONOMY and assert it is rejected or fully resolved.
