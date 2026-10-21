CQL on Databricks — Databricks Industry Solution Accelerator
Copyright (2026) Databricks, Inc.

This Software includes software developed at Databricks (https://www.databricks.com/) and its use is
subject to the included LICENSE file.

## Support

Databricks does not offer official support for this solution accelerator or any demo assets. It is
provided as-is, best-effort, for demonstration and evaluation only. For any issue, please open a GitHub
issue and the team will review on a best-effort basis.

## Data in this repository

All patient data in this repository is synthetic. There are no real patients and no PHI.

| Data | Location | How it was made |
|---|---|---|
| Small fixtures and sample patients | `compiler/conformance/src/test/resources/data`, `examples/data` | Written for this repository. |
| Synthetic populations | Not stored in this repository | Generated in your workspace from measure logic, by `loader/synthesizer` and `loader/scripts/synthetic-population`. |

## Value sets and terminology

Value-set definitions are not included. The small fixtures include the few expansions their tests need, as
`valueset-*.ndjson` files written for this repository.

The fixtures list codes from the code systems below. They hold only the codes that the tests need. This
repository is not a terminology distribution, and use of these codes is subject to each code system's
license.

  - CPT® is copyright American Medical Association. All rights reserved. CPT is a
    registered trademark of the American Medical Association. Fee schedules,
    relative value units, conversion factors, and/or related components are not
    assigned by the AMA, are not part of CPT, and the AMA is not recommending
    their use.
  - SNOMED CT® is copyright SNOMED International. In the United States, SNOMED CT
    is distributed by the National Library of Medicine under the UMLS
    Metathesaurus License; use may require a UMLS Terminology Services account.
  - LOINC® is copyright Regenstrief Institute, Inc. and the LOINC Committee, and
    is available at no cost under the LOINC license.
  - ICD-10-CM and HCPCS are published by the U.S. Centers for Medicare & Medicaid
    Services and the National Center for Health Statistics.
  - RxNorm is produced by the U.S. National Library of Medicine.
  - UCUM (Unified Code for Units of Measure) is copyright Regenstrief Institute,
    Inc. and the UCUM Organization.

## Third-party material in this repository

| Material | Location | Source and license |
|---|---|---|
| CQL operator tests | `compiler/conformance/src/test/resources/cql-tests/` | Four files from [cqframework/cql-tests](https://github.com/cqframework/cql-tests) at commit `06667083335f645e46e6f88bbd3b4d8d232574df`, unmodified. Apache License 2.0. |
| FHIR R4 schemas | `compiler/core/model-schemas/fhir-r4/` | HL7 FHIR R4 XML schemas. Creative Commons "No Rights Reserved" (CC0). |

Third-party libraries this repository depends on are listed, with their licenses, in `README.md`.

## Measure content

This repository contains no published eCQM content. Its measures and libraries are written for this
repository.

## HL7 standards

CQL (Clinical Quality Language), ELM, FHIR®, and QI-Core are standards of Health Level Seven International.
FHIR is a registered trademark of HL7 and is used with permission. HL7 standards material, including the
FHIR R4 schemas, is licensed by HL7 under Creative Commons "No Rights Reserved" (CC0).

Trademarks are the property of their respective owners.
