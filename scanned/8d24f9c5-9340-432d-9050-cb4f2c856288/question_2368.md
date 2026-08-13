# Q2368: cpi_transfer_withdraw_intermediary_ata_to_destination: withdraw intermediary flow can be replayed or interrupted [boundary-sized-withdrawals-near-one] [round-trip]

## Question
Can an unprivileged attacker make `juplend_withdraw` drive `cpi_transfer_withdraw_intermediary_ata_to_destination` with boundary-sized withdrawals near one-unit residual transfers so an intermediary withdraw flow can be replayed, interrupted, or finalized twice, violating `intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable` and causing `Critical: direct theft of redeemed assets`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/juplend/withdraw.rs` / `cpi_transfer_withdraw_intermediary_ata_to_destination`
- Entrypoint: `juplend_withdraw`
- Attacker controls: boundary-sized withdrawals near one-unit residual transfers
- Exploit idea: Audit multi-hop withdraws that pass through temporary ATAs or protocol-owned accounts before reaching the user. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: intermediary ATA closeout after Juplend withdraw must be single-use, canonical, and non-redirectable
- Expected Immunefi impact: Critical: direct theft of redeemed assets
- Fast validation: Replay or fail at each hop and assert no hop can be repeated or left value-bearing without a single canonical finalization. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
