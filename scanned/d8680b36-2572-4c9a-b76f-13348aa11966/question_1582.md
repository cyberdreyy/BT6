# Q1582: cpi_transfer_obligation_owner_to_destination: withdraw refresh and redeem operate on different external state [candidate-destinations-from-another-bank] [round-trip]

## Question
Can an unprivileged attacker invoke `kamino_withdraw` with candidate destinations from another bank or integration family so `cpi_transfer_obligation_owner_to_destination` refreshes one external state object but redeems another, violating `final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value` and leading to `Critical: direct theft of withdrawn assets`? Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.

## Target
- File/function: `programs/marginfi/src/instructions/kamino/withdraw.rs` / `cpi_transfer_obligation_owner_to_destination`
- Entrypoint: `kamino_withdraw`
- Attacker controls: candidate destinations from another bank or integration family
- Exploit idea: Where refresh precedes withdraw, ensure the refreshed reserve/obligation/position is exactly the one later burned or redeemed. Focus specifically on withdraw/deposit round-trips around the integration boundary for asymmetric conversions.
- Invariant to test: final transfer-out after Kamino redemption must be bound to the canonical recipient and cannot replay or redirect value
- Expected Immunefi impact: Critical: direct theft of withdrawn assets
- Fast validation: Feed mismatched external contexts and assert withdraw rejects unless refresh and redeem are bound to the same object. Run deposit-then-withdraw and withdraw-then-deposit loops and assert no positive attacker value emerges.
