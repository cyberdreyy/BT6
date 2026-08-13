# Q1080: maybe_take_bank_mint: amount utility treats zero or near-zero values unsafely in a live path [duplicate-metas-affecting-remaining-account] [family-binding]

## Question
Can an unprivileged attacker use `lending_pool_handle_bankruptcy` with duplicate metas affecting remaining-account consumption order so `maybe_take_bank_mint` treats zero or near-zero values unsafely in a live value-moving path, breaking `mint selection for live value-moving paths must bind to the exact bank and cannot be redirected by caller-supplied remaining accounts` and causing `High: wrong mint context causing fee drift, misrouting, or protocol loss`? Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.

## Target
- File/function: `programs/marginfi/src/utils/general.rs` / `maybe_take_bank_mint`
- Entrypoint: `lending_pool_handle_bankruptcy`
- Attacker controls: duplicate metas affecting remaining-account consumption order
- Exploit idea: Search utility boundaries used by production instructions for tiny-value behavior that can unlock rounding extraction or permanent locks. Focus specifically on cross-family substitution between liquidity, fee, insurance, and integration utility contexts.
- Invariant to test: mint selection for live value-moving paths must bind to the exact bank and cannot be redirected by caller-supplied remaining accounts
- Expected Immunefi impact: High: wrong mint context causing fee drift, misrouting, or protocol loss
- Fast validation: Fuzz zero-threshold edges through the consuming instructions and assert no value leak or stuck state is created. Cross-substitute same-interface vaults, mints, and authorities and assert no utility-backed path accepts a foreign family.
