// SPDX-License-Identifier: MIT
pragma solidity 0.8.30;

/// @notice Append-only SHA-256 commitments to off-chain FireWatch evidence.
/// @dev A commitment proves neither incident reality nor model validity.
contract AlertRegistry {
    error InvalidWriter();
    error UnauthorizedWriter();
    error EmptyAlertKey();
    error EmptyEvidenceDigest();
    error ConflictingDigest(bytes32 alertKey, bytes32 storedDigest, bytes32 suppliedDigest);

    address public immutable writer;
    mapping(bytes32 alertKey => bytes32 digest) private digests;

    /// @dev Model version and other provenance are bound inside the hashed off-chain payload.
    event EvidenceAnchored(
        bytes32 indexed alertKey, bytes32 indexed evidenceDigest, address indexed writer
    );

    constructor(address authorizedWriter) {
        if (authorizedWriter == address(0)) revert InvalidWriter();
        writer = authorizedWriter;
    }

    /// @return created True for the first write; false for an identical authorized retry.
    function anchor(bytes32 alertKey, bytes32 digest) external returns (bool created) {
        if (msg.sender != writer) revert UnauthorizedWriter();
        if (alertKey == bytes32(0)) revert EmptyAlertKey();
        if (digest == bytes32(0)) revert EmptyEvidenceDigest();
        bytes32 stored = digests[alertKey];
        if (stored != bytes32(0)) {
            if (stored != digest) {
                revert ConflictingDigest(alertKey, stored, digest);
            }
            return false;
        }
        digests[alertKey] = digest;
        emit EvidenceAnchored(alertKey, digest, msg.sender);
        return true;
    }

    /// @notice Zero denotes no entry; zero digests cannot be written.
    function evidenceDigest(bytes32 alertKey) external view returns (bytes32) {
        return digests[alertKey];
    }
}
