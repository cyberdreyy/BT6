# Q1587: cpi_transfer_obligation_owner_to_destination: withdraw intermediary flow can be replayed or interrupted [same-slot-withdraw-plus-harvest] [recipient-binding]

## Question
Can an unprivileged attacker make `kamino_withdraw` drive `cpi_transfer_obligation_owner_to_destination` with same-slot withdraw plus harvest or another transfer-using path so an intermediary withdraw flow can be replayed, interrupted, or finalized twice, violating `final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value` and causing `Critical: direct theft of withdrawn assets`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `cpi_transfer_obligation_owner_to_destination`
- Entrypoint: `kamino_withdraw`
- Attacker controls: same-slot withdraw plus harvest or another transfer-using path
- Exploit idea: Audit multi-hop withdraws that pass through temporary ATAs or protocol-owned accounts before reaching the user. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value
- Expected Immunefi impact: Critical: direct theft of withdrawn assets
- Fast validation: Replay or fail at each hop and assert no hop can be repeated or left value-bearing without a single canonical finalization. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
