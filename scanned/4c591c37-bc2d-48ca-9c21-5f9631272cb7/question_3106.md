# Q3106: lending_pool_configure_bank_oracle: price-cache or fixed-price mode switch leaves unsafe mixed state [an-attacker-signer-with-a] [identity-vs-shape]

## Question
Can an unprivileged attacker make `lending_pool_configure_bank_oracle` reach `lending_pool_configure_bank_oracle` with an attacker signer with a victim bank and attacker-chosen oracle key so a price mode switch leaves unsafe mixed state, violating `oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended` and leading to `Critical: attacker-installed pricing enabling theft or insolvency`? Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.

## Target
- File/function: `programs/marginfi/src/instructions/marginfi_group/config_bank_oracle.rs` / `lending_pool_configure_bank_oracle`
- Entrypoint: `lending_pool_configure_bank_oracle`
- Attacker controls: an attacker signer with a victim bank and attacker-chosen oracle key
- Exploit idea: Check whether switching oracle modes or staked onramp settings fully invalidates or refreshes dependent cached state. Focus specifically on exact oracle identity and lineage, not merely owner/type compatibility.
- Invariant to test: oracle configuration changes must require exact authority and bind to the exact bank/oracle lineage intended
- Expected Immunefi impact: Critical: attacker-installed pricing enabling theft or insolvency
- Fast validation: Perform the controlled switch, then immediately run dependent user actions and assert they see a single coherent pricing mode. Substitute same-type oracle-related accounts and assert every non-canonical identity is rejected even if shape-compatible.
