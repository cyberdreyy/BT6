[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** crates/aptos-crypto/src/bls12381/bls12381_keys.rs (L227-247)
```rust
impl TryFrom<&[u8]> for PublicKey {
    type Error = CryptoMaterialError;

    /// Deserializes a PublicKey from a sequence of bytes.
    ///
    /// WARNING: Does NOT subgroup-check the public key! Instead, the caller is responsible for
    /// verifying the public key's proof-of-possession (PoP) via `ProofOfPossession::verify`,
    /// which implicitly subgroup-checks the public key.
    ///
    /// NOTE: This function will only check that the PK is a point on the curve:
    ///  - `blst::min_pk::PublicKey::from_bytes(bytes)` calls `blst::min_pk::PublicKey::deserialize(bytes)`,
    ///    which calls `$pk_deser` in <https://github.com/supranational/blst/blob/711e1eec747772e8cae15d4a1885dd30a32048a4/bindings/rust/src/lib.rs#L734>,
    ///    which is mapped to `blst_p1_deserialize` in <https://github.com/supranational/blst/blob/711e1eec747772e8cae15d4a1885dd30a32048a4/bindings/rust/src/lib.rs#L1652>
    ///  - `blst_p1_deserialize` eventually calls `POINTonE1_Deserialize_BE`, which checks
    ///    the point is on the curve: <https://github.com/supranational/blst/blob/711e1eec747772e8cae15d4a1885dd30a32048a4/src/e1.c#L296>
    fn try_from(bytes: &[u8]) -> std::result::Result<Self, CryptoMaterialError> {
        Ok(Self {
            pubkey: blst::min_pk::PublicKey::from_bytes(bytes)
                .map_err(|_| CryptoMaterialError::DeserializationError)?,
        })
    }
```

**File:** crates/aptos-crypto/src/validatable.rs (L59-82)
```rust
    /// Create a new `Validatable` from an unvalidated type
    pub fn from_unvalidated(unvalidated: V::Unvalidated) -> Self {
        Self {
            unvalidated,
            maybe_valid: OnceCell::new(),
        }
    }

    /// Return a reference to the unvalidated form `V::Unvalidated`
    pub fn unvalidated(&self) -> &V::Unvalidated {
        &self.unvalidated
    }

    /// Try to validate the unvalidated form, returning `Some(&V)` on success and `None` on failure.
    pub fn valid(&self) -> Option<&V> {
        self.validate().ok()
    }

    // TODO maybe optimize to only try once and keep track when we fail. This would avoid multiple calls to validate() by valid() when validation fails
    /// Attempt to validate `V::Unvalidated` and return a reference to a valid `V`
    pub fn validate(&self) -> Result<&V> {
        self.maybe_valid
            .get_or_try_init(|| V::validate(&self.unvalidated))
    }
```

**File:** crates/aptos-crypto/src/unit_tests/bls12381_test.rs (L336-371)
```rust
#[test]
fn bls12381_validatable_pk() {
    let mut rng = OsRng;

    // Test that prime-order points pass the validate() call
    let keypair = KeyPair::<PrivateKey, PublicKey>::generate(&mut rng);
    let pk_bytes = keypair.public_key.to_bytes();

    let validatable = Validatable::from_validated(keypair.public_key);

    assert!(validatable.validate().is_ok());
    assert_eq!(validatable.validate().unwrap().to_bytes(), pk_bytes);

    // Test that low-order points don't pass the validate() call
    //
    // Low-order points were sampled from bls12_381 crate (https://github.com/zkcrypto/bls12_381/blob/main/src/g1.rs)
    // - The first point was convereted from projective to affine coordinates and serialized via `point.to_affine().to_compressed()`.
    // - The second point was in affine coordinates and serialized via `a.to_compressed()`.
    let low_order_points = [
        "ae3cd9403b69c20a0d455fd860e977fe6ee7140a7f091f26c860f2caccd3e0a7a7365798ac10df776675b3a67db8faa0",
        "928d4862a40439a67fd76a9c7560e2ff159e770dcf688ff7b2dd165792541c88ee76c82eb77dd6e9e72c89cbf1a56a68",
    ];

    for p in low_order_points {
        let point = hex::decode(p).unwrap();
        assert_eq!(point.len(), PublicKey::LENGTH);

        let pk = PublicKey::try_from(point.as_slice()).unwrap();

        // First, make sure group_check() identifies this point as a low-order point
        assert!(pk.subgroup_check().is_err());

        // Second, make sure our Validatable<PublicKey> implementation agrees with group_check
        let validatable = Validatable::<PublicKey>::from_unvalidated(pk.to_unvalidated());
        assert!(validatable.validate().is_err());
    }
```
