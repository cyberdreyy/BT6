# Q2: derive_single_pool_keys_from_vote: derived address can be confused across economic contexts [two-validator-vote-accounts-with] [runtime-recheck]

## Question
Can an unprivileged attacker exploit two validator vote accounts with similar surrounding context so `derive_single_pool_keys_from_vote` derives or accepts the same-looking PDA/address across different economic contexts, violating `validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context` and causing `High: wrong staked collateral binding leading to mispricing or durable value lock`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_single_pool_keys_from_vote`
- Entrypoint: `lending_pool_add_bank_permissionless`
- Attacker controls: two validator vote accounts with similar surrounding context
- Exploit idea: Audit seed domain separation for every derived authority, vault, reserve, or staked helper address used by public flows. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: validator-vote-derived staked collateral PDAs must be unique to the exact validator and pool context
- Expected Immunefi impact: High: wrong staked collateral binding leading to mispricing or durable value lock
- Fast validation: Generate derivations across neighboring contexts and assert no public path accepts a PDA derived for another context. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
