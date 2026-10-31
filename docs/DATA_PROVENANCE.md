# Input Data Provenance and Licensing Review

## Purpose

This document records verified information about the frozen
geographic inputs used in the simulation study:

*A Reproducible Energy-Aware Benchmarking Framework for
Real-Time Assignment in Autonomous UGV Fleets.*

It distinguishes established provenance from unresolved questions.
This document does not modify the experimental inputs.

## 1. Road network

File: `inputs/utp_graph.graphml`

SHA-256:
`ebd138a83763f11172e079f40457b371be7eebcd2e3aa018aeda13d9d70114f4`

Recorded characteristics:

- Nodes: 474
- Edges: 1,237
- Coordinate reference system: EPSG:4326
- Simplified graph: True
- GraphML `created_with`: OSMnx 2.1.0
- GraphML `created_date`: 2026-07-24 20:27:52
- Time zone of the recorded date: not established

### Source evidence

Historical simulator files contain explicit code to download
the UTP road network from OpenStreetMap using
`ox.graph_from_place()` and to save it using
`ox.save_graphml()`.

Examples include:

- `app_before_genetic_rng_fix.py`
- `app_before_parking_batch_update.py`
- `app_stage1a_working.py`

Historical versions show different `network_type` settings,
including `drive` and `all`. Some also convert the graph
to an undirected representation and process connected
components.

The historical source code supports OpenStreetMap-derived
network provenance, but does not establish which exact
historical revision generated the frozen GraphML file.

The exact original geographic query, OpenStreetMap snapshot,
and complete preprocessing sequence are not yet independently
reconstructed.

### OpenStreetMap attribution

Map data: © OpenStreetMap contributors.

Source and copyright information:
https://www.openstreetmap.org/copyright

OpenStreetMap data is provided under the Open Database
License (ODbL):
https://opendatacommons.org/licenses/odbl/1-0/

Any redistribution or adaptation of the OSM-derived network
must follow applicable ODbL conditions, including attribution
and any relevant share-alike requirements.

This attribution does not determine the license for the
independently developed simulator source code.

## 2. Hotspot locations

File: `inputs/hotspot_locations.csv`

SHA-256:
`6a55a275f2ac18ab383d2efcd67a31250602503a62fa3c5bf2f3f504ae0b979d`

Dataset columns:

- name
- lat
- lon
- category
- demand_weight
- node

Recorded observations:

- Hotspot records: 87
- Valid graph-node references: 87
- Unmatched graph-node references: 0

The original project audit located 1,147 copies of this
filename, all matching the accepted SHA-256. This establishes
consistency among the audited copies, not original authorship.

Historical simulator code loads the CSV using pandas.
The original hotspot creation procedure and geographic
data source have not been established.

Potential sources requiring author confirmation include
manual annotation, campus-provided records, and externally
sourced geographic information. No one source is asserted.

### Preserved data characteristics

The frozen input contains:

- `Hostel` and `Hostel ` as separate raw category strings
- `Lab` and `lab` as separate raw category strings
- One blank category value

These values are preserved exactly because they belong to
the accepted experimental input.

This document does not claim that the category variations
affected the algorithms or experimental outcomes.

## 3. Reproducibility implications

The frozen network and hotspot input files are identified by
exact SHA-256 digests. They should be used unchanged when
reproducing the accepted experimental conditions.

Downloading a fresh OpenStreetMap network may produce a
different graph due to source updates, geographic query
differences, or preprocessing variations.

The accepted inputs must not be silently replaced by newly
generated geographic data.

## 4. Licensing and publication decisions pending

Before making the repository public:

1. Confirm the exact frozen-network generation procedure
   where original logs or scripts permit.
2. Confirm how the hotspot locations and coordinates were
   originally collected or created.
3. Determine whether any campus or third-party permission
   applies to the hotspot dataset.
4. Include appropriate OpenStreetMap attribution in the
   paper, repository, and relevant figures.
5. Approve software and dataset licenses separately.
6. Confirm author and contributor attribution before
   preparing CITATION.cff.

No repository-wide LICENSE has been selected by this
document. No independent ownership claim is made over
OpenStreetMap data or unidentified third-party data.

## 5. Source libraries

OSMnx: https://osmnx.readthedocs.io/

OpenStreetMap: https://www.openstreetmap.org/

The OSMnx documentation recommends citing the relevant
software publication when OSMnx is used in research.
The bibliographic entry should be verified before insertion
into the manuscript.

## 6. Audit status

The network and hotspot identities, sizes, and structural
checks were verified in the GITHUB-13B through GITHUB-13E
local audits. Original research files and frozen simulator
were not modified during those audits.
