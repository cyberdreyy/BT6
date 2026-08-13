# Q494: derive_juplend_supply_position: vault derivation helper can point value-moving code at the wrong vault family [candidate-pdas-from-a-sibling] [runtime-recheck]

## Question
Can an unprivileged attacker use `juplend_deposit` with candidate PDAs from a sibling market with similar seeds so `derive_juplend_supply_position` points value-moving code at the wrong vault family via derivation confusion, breaking `Juplend supply-position derivation must be unique to the intended owner, bank, and external market context` and causing `High: value redirected to the wrong external position or later reward theft`? Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.

## Target
- File/function: `type-crate/src/pdas.rs` / `derive_juplend_supply_position`
- Entrypoint: `juplend_deposit`
- Attacker controls: candidate PDAs from a sibling market with similar seeds
- Exploit idea: Audit helpers used to derive liquidity, insurance, fee, and intermediary vaults and their later binding in transfer paths. Focus specifically on whether runtime constraints recompute the PDA instead of trusting caller-supplied prederived addresses.
- Invariant to test: Juplend supply-position derivation must be unique to the intended owner, bank, and external market context
- Expected Immunefi impact: High: value redirected to the wrong external position or later reward theft
- Fast validation: Cross-substitute vault-family PDAs and assert no transfer path accepts a vault from another family. Supply attacker-prederived candidates and assert runtime rejects everything except the exact recomputed PDA.
