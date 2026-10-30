# Vendored FHIR R4 XSD schema set (`BUILD-FHIR-CODEGEN`)

These XSDs are a **committed build input**: the `generateFhirModel` Gradle task parses them at
build time to emit the static FHIR element model the engine types/lowers against
(`doc/fhir-static-typing.md`, requirements `BUILD-FHIR-CODEGEN` / `TYPE-FHIR-MODEL`). The build
host has no network access, so the schemas are vendored here rather than downloaded at build time.
Codegen never fetches; it only reads these files.

## Provenance (pinned)

- **FHIR version:** R4 — `4.0.1` (the `version="4.0.1"` attribute on the schema root).
- **Source:** HL7 FHIR R4 XSD package — <https://hl7.org/fhir/R4/fhir-all-xsd.zip>
  (download index: <https://fhir.hl7.org/fhir/downloads.html>).
- **Retrieved:** 2026-06-20.
- **Contents:** the official `.xsd` set unzipped verbatim (per-resource schemas, `fhir-base.xsd`,
  the self-contained `fhir-single.xsd`, `fhir-all.xsd`, and `xml.xsd`). The Schematron (`.sch`)
  files from the zip are intentionally not vendored — codegen needs only the XSDs.
- **Primary parse input:** `fhir-single.xsd` (self-contained: every `complexType` in one file, no
  `xs:include` resolution needed). Checksum (sha256):
  `aa40cefca6e6c8b2740da41dbe12c0d627d3ffaa2de7887b36865b2998c09506`.

## Scope boundary

Base FHIR R4 XSDs model the base resources/datatypes only. **Profile** constraints/extensions
(e.g. the us-core-sex extension a measure reads for `Patient.sex`) are NOT in the base XSD and stay
hand-modeled in the engine mapping layer (`sema/FhirModel`). The engine resolves profiles → base
at retrieve, so the base XSD is sufficient for navigation / value[x] / schema completeness.

To re-vendor (e.g. a FHIR version bump): download the official `fhir-all-xsd.zip` for that
version, unzip, copy the `.xsd` files here, update the provenance above, and re-run
`./gradlew :compiler:generateFhirModel`.
