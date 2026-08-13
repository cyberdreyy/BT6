# Q2365: cpi_transfer_withdraw_intermediary_ata_to_destination: withdraw intermediary flow can be replayed or interrupted [candidate-intermediary-accounts-from-another] [recipient-binding]

## Question
Can an unprivileged attacker make `juplend_withdraw` drive `cpi_transfer_withdraw_intermediary_ata_to_destination` with candidate intermediary accounts from another bank or integration family so an intermediary withdraw flow can be replayed, interrupted, or finalized twice, violating `intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable` and causing `Critical: direct theft of redeemed assets`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `cpi_transfer_withdraw_intermediary_ata_to_destination`
- Entrypoint: `juplend_withdraw`
- Attacker controls: candidate intermediary accounts from another bank or integration family
- Exploit idea: Audit multi-hop withdraws that pass through temporary ATAs or protocol-owned accounts before reaching the user. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable
- Expected Immunefi impact: Critical: direct theft of redeemed assets
- Fast validation: Replay or fail at each hop and assert no hop can be repeated or left value-bearing without a single canonical finalization. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
