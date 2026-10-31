# Public-release preparation and provenance

## Preservation boundary

The accepted 60-file research release was left unchanged.
A separate public candidate was prepared.

The following original archive packages were not copied:

- Primary compact evidence ZIP
- S5 compact evidence ZIP
- Frozen simulator bundle ZIP
- Nested S5 analysis ZIP

They remain preserved with their original checksums
outside this public-release candidate.

## Public derivatives

The two run-level CSV files have their `archive` field
converted from a machine-local absolute path into the
archive path relative to `research_revision_v2`.

Each converted archive identity was checked against the
corresponding original archive index.

Three JSON metadata files replace the local project-root
prefix with `${PROJECT_ROOT}`.

Source and destination SHA-256 values are separately
listed in `verification/public_file_derivations.csv`.

Changing the representation of a path changes the file
hash even when numerical research outcomes are unchanged.

## Original checksum inventories

The original archive checksum inventories are included
as provenance evidence. Their entries do not imply that
all original raw files are distributed here.

## Reproduction limits

Reproducing the original 1,000 simulation runs requires
the archived inputs, correct dependencies, appropriate
configuration, and original campaign execution procedure.

Reproducing the published numerical analysis requires
the relevant validated datasets and analytical scripts.

Portability across computers and full clean-environment
execution have not yet been independently demonstrated.

## Pending before public publication

Review privacy and permissions, verify the repository
contents, select a license, add full reproduction
instructions, and deposit or document the original raw
archives and their access conditions.
