[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** third_party/move/move-core/types/src/account_address.rs (L161-179)
```rust
    pub fn from_hex_literal(literal: &str) -> Result<Self, AccountAddressParseError> {
        if !literal.starts_with("0x") {
            return Err(AccountAddressParseError::LeadingZeroXRequired);
        }

        let hex_len = literal.len() - 2;

        // If the string is too short, pad it
        if hex_len < Self::LENGTH * 2 {
            let mut hex_str = String::with_capacity(Self::LENGTH * 2);
            for _ in 0..Self::LENGTH * 2 - hex_len {
                hex_str.push('0');
            }
            hex_str.push_str(&literal[2..]);
            AccountAddress::from_hex(hex_str)
        } else {
            AccountAddress::from_hex(&literal[2..])
        }
    }
```

**File:** third_party/move/move-core/types/src/account_address.rs (L373-404)
```rust
    /// NOTE: This function has relaxed parsing behavior. For strict behavior, please use
    /// the `from_str_strict` function. Where possible use `from_str_strict` rather than
    /// this function.
    ///
    /// Create an instance of AccountAddress by parsing a hex string representation.
    ///
    /// This function allows all formats defined by AIP-40. In short this means the
    /// following formats are accepted:
    ///
    /// - LONG, with or without leading 0x
    /// - SHORT, with or without leading 0x
    ///
    /// Where:
    ///
    /// - LONG is 64 hex characters.
    /// - SHORT is 1 to 63 hex characters inclusive.
    ///
    /// Learn more about the different address formats by reading AIP-40:
    /// <https://github.com/aptos-foundation/AIPs/blob/main/aips/aip-40.md>.
    fn from_str(s: &str) -> Result<Self, AccountAddressParseError> {
        if !s.starts_with("0x") {
            if s.is_empty() {
                return Err(AccountAddressParseError::TooShort);
            }
            AccountAddress::from_hex_literal(&format!("0x{}", s))
        } else {
            if s.len() == 2 {
                return Err(AccountAddressParseError::TooShort);
            }
            AccountAddress::from_hex_literal(s)
        }
    }
```

**File:** third_party/move/move-core/types/src/account_address.rs (L700-784)
```rust
    #[test]
    fn test_account_address_from_str() {
        assert_eq!(
            &AccountAddress::from_str("0x0")
                .unwrap()
                .to_standard_string(),
            "0x0"
        );
        assert_eq!(
            &AccountAddress::from_str("0x1")
                .unwrap()
                .to_standard_string(),
            "0x1"
        );
        assert_eq!(
            &AccountAddress::from_str("0xf")
                .unwrap()
                .to_standard_string(),
            "0xf"
        );
        assert_eq!(
            &AccountAddress::from_str("0x0f")
                .unwrap()
                .to_standard_string(),
            "0xf"
        );
        assert_eq!(
            &AccountAddress::from_str("0x010")
                .unwrap()
                .to_standard_string(),
            "0x0000000000000000000000000000000000000000000000000000000000000010"
        );
        assert_eq!(
            &AccountAddress::from_str("0xfdfdf")
                .unwrap()
                .to_standard_string(),
            "0x00000000000000000000000000000000000000000000000000000000000fdfdf"
        );
        assert_eq!(
            &AccountAddress::from_str(
                "0x0500000000000000000000000000000000000000000000000000000000aadfdf"
            )
            .unwrap()
            .to_standard_string(),
            "0x0500000000000000000000000000000000000000000000000000000000aadfdf"
        );

        // As above but without the 0x prefix.
        assert_eq!(
            &AccountAddress::from_str("0").unwrap().to_standard_string(),
            "0x0"
        );
        assert_eq!(
            &AccountAddress::from_str("1").unwrap().to_standard_string(),
            "0x1"
        );
        assert_eq!(
            &AccountAddress::from_str("f").unwrap().to_standard_string(),
            "0xf"
        );
        assert_eq!(
            &AccountAddress::from_str("0f").unwrap().to_standard_string(),
            "0xf"
        );
        assert_eq!(
            &AccountAddress::from_str("010")
                .unwrap()
                .to_standard_string(),
            "0x0000000000000000000000000000000000000000000000000000000000000010"
        );
        assert_eq!(
            &AccountAddress::from_str("fdfdf")
                .unwrap()
                .to_standard_string(),
            "0x00000000000000000000000000000000000000000000000000000000000fdfdf"
        );
        assert_eq!(
            &AccountAddress::from_str(
                "0500000000000000000000000000000000000000000000000000000000aadfdf"
            )
            .unwrap()
            .to_standard_string(),
            "0x0500000000000000000000000000000000000000000000000000000000aadfdf"
        );
    }
```
