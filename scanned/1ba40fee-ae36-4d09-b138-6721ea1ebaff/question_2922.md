# Q2922: unified-wallet detection flips custody in smart-wallets.ts

## Question
isUnifiedWallet returns true only when account.id exists and recovery_method === 'privy-v2'; can an attacker present an account object that flips this predicate so smart-wallets entry (BICONOMY routes signing through the wrong custody path?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Pass an account with an id but a different recovery_method, and vice versa.
- Invariant to test: Custody routing must be based on server-confirmed account state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass crafted account objects to smart-wallets entry (BICONOMY and assert re-validation.
