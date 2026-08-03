[1](#0-0)

### Citations

**File:** aptos-move/framework/aptos-framework/sources/account/account.move (L37-58)
```text
    #[event]
    struct KeyRotationToPublicKey has drop, store {
        // The address of the account that is rotating its key
        account: address,
        // The bitmap of verified public keys.  This indicates which public keys have been verified by the account owner.
        // The bitmap is 4 bytes long, thus representing 32 bits.  Each bit represents whether a public key has been verified.
        // In the 32 bit representation, if a bit at index i (read left to right) is 1, then the public key at index i has
        // been verified in the public key.
        //
        // For example: [0x10100000,0x00000000,0x00000000,0x00000000] marks the first and third public keys in the multi-key as verified.
        //
        // Note: In the case of a single key, only the first bit is used.
        verified_public_key_bit_map: vector<u8>,
        // The scheme of the public key.
        public_key_scheme: u8,
        // The byte representation of the public key.
        public_key: vector<u8>,
        // The old authentication key on the account
        old_auth_key: vector<u8>,
        // The new authentication key which is the hash of [public_key, public_key_scheme]
        new_auth_key: vector<u8>,
    }
```
