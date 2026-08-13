# Q1581: cpi_transfer_obligation_owner_to_destination: withdraw refresh and redeem operate on different external state [candidate-destinations-from-another-bank] [recipient-binding]

## Question
Can an unprivileged attacker invoke `kamino_withdraw` with candidate destinations from another bank or integration family so `cpi_transfer_obligation_owner_to_destination` refreshes one external state object but redeems another, violating `final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value` and leading to `Critical: direct theft of withdrawn assets`? Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `cpi_transfer_obligation_owner_to_destination`
- Entrypoint: `kamino_withdraw`
- Attacker controls: candidate destinations from another bank or integration family
- Exploit idea: Where refresh precedes withdraw, ensure the refreshed reserve/obligation/position is exactly the one later burned or redeemed. Focus specifically on destination binding across intermediary ATAs, vault authorities, and final transfer-out.
- Invariant to test: final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value
- Expected Immunefi impact: Critical: direct theft of withdrawn assets
- Fast validation: Feed mismatched external contexts and assert withdraw rejects unless refresh and redeem are bound to the same object. Swap every plausible destination and assert no accepted path releases value to an unvalidated recipient.
