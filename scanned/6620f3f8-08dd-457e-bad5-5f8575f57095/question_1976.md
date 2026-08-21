# Q1976: token amount formatting trusts decimals in getUserSmartWallet.ts

## Question
formatTokenAmount formats with a caller-supplied decimals value; can an attacker pass a wrong decimals through getUserSmartWallet: first linked account of type smart_wallet so the displayed amount differs from the transferred amount by orders of magnitude?

## Target
- File/function: [src/utils/getUserSmartWallet.ts](src/utils/getUserSmartWallet.ts) - getUserSmartWallet: first linked account of type smart_wallet
- Entrypoint: smart-wallet routing and linking
- Attacker controls: linked_accounts contents including multiple smart wallets
- Exploit idea: Pass a decimals value that does not match the token.
- Invariant to test: Decimals must be derived from the token record.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass mismatched decimals to getUserSmartWallet: first linked account of type smart_wallet and assert derivation or rejection.
