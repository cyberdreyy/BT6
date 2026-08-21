# Q1602: EIP712Domain type rebuilt from present keys in smart-wallets.ts

## Question
generateDomainType reconstructs the EIP712Domain field list from whichever domain keys are present; can an attacker omit or add domain fields through smart-wallets entry (BICONOMY so the hashed domain differs from what the verifier expects?

## Target
- File/function: [src/smart-wallets.ts](src/smart-wallets.ts) - smart-wallets entry (BICONOMY, COINBASE_SMART_WALLET, KERNEL, LIGHT_ACCOUNT, SAFE, THIRDWEB, NEXUS)
- Entrypoint: import {...} from '@privy-io/js-sdk-core/smart-wallets'
- Attacker controls: smart wallet type/version strings used for linking and routing
- Exploit idea: Submit a domain with salt but no chainId, or with an unknown extra key.
- Invariant to test: Domain type construction must match the domain object exactly and reject unknown keys.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: enumerate domain key subsets through smart-wallets entry (BICONOMY and assert the generated type list matches.
