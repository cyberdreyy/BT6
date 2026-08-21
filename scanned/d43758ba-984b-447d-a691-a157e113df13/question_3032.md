# Q3032: wallet-standard features called with an injected account in smart-wallets.ts

## Question
ConnectedStandardSolanaWallet spreads `{...input, account: this.#t}` into every feature call; can an attacker construct the wrapper with an account that does not match the underlying wallet so smart-wallets entry (BICONOMY requests signatures for a foreign account?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Construct the wrapper with a mismatched account/wallet pair.
- Invariant to test: The wrapped account must be verified to belong to the wrapped wallet.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: construct smart-wallets entry (BICONOMY with a mismatched pair and assert construction fails.
