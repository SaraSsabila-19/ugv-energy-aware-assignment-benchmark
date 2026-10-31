# Research Release Checklist

## Verified

- [x] Frozen FIX-19 simulator identity checked
- [x] Primary 640-run manifest retained
- [x] S5 160-run manifest retained
- [x] Primary and S5 summary data verified
- [x] S5-to-S4 workload matching checked
- [x] Primary controller identified with provenance caveat
- [x] S5 controller authenticated against checkpoint
- [x] Original source and analysis code preserved
- [x] Five public data transformations documented
- [x] Original archive checksum inventories retained
- [x] Recorded package environment documented
- [x] Selected credential and local-path scans passed
- [x] Read-only summary-data verification script created

## Before public publication

- [ ] Manually review source and data for sensitive content
- [ ] Confirm permissions for road-network GraphML redistribution
- [ ] Confirm hotspot-data redistribution rights
- [ ] Select suitable source-code and data licenses
- [ ] Create LICENSE using the approved license
- [ ] Confirm software contributors and manuscript authors
- [ ] Create CITATION.cff with confirmed attribution
- [ ] Test dependency installation in a clean environment
- [ ] Test portable execution of the analysis programs
- [ ] Validate figure and table regeneration procedures
- [ ] Establish access conditions for complete raw archives
- [ ] Add archival DOI or persistent research-data identifier
- [ ] Check the manuscript's data-availability statement
- [ ] Review the actual remote GitHub repository
- [ ] Tag and archive the final verified release

## Interpretation limits

The released data support reproducibility checks, but
do not yet constitute independent end-to-end replication.

The benchmark provides simulation evidence and does
not establish physical UGV validity or hard real-time
execution guarantees.
