# Private transfer directory

Place only reviewed versioned release packages here for explicit operator import.
Contents other than this README are ignored by Git and excluded from Docker builds.
The directory is mounted read-only at `/imports` in the optional operator service.
Never place a wallet or `.env` here. Model import requires the separately trusted
SHA-256 of `release-manifest.json` and does not grant registry approval.
