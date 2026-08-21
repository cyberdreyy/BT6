# Q3582: token account picked with .at(0) in smart-wallets.ts

## Question
getTokenAccountsByOwner takes the first returned account's parsed amount; can an attacker cause multiple token accounts to be returned so smart-wallets entry (BICONOMY reports a balance from an account the user does not control?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Return several accounts including a zero-balance decoy first.
- Invariant to test: Balance aggregation must consider every matching account.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return multiple accounts from smart-wallets entry (BICONOMY's RPC stub and assert correct aggregation.
