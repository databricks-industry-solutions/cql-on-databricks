/*
@description: Adults 45 to 75 who are current on colorectal cancer screening.
@intent: A sample proportion measure, written for this repository, where each screening test has its own
  lookback: a colonoscopy counts for ten years, a stool test for one.
@scope: QI-Core 6.0.0. A qualifying outpatient visit in the measurement period.
@nonGoal: Not a published measure. Screening ordered but not done does not count.
@guidance: Higher is better. Each lookback ends at the end of the measurement period. The measurement period is
  passed in at run time.
*/
library ColorectalScreening version '1.0.0'

using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers
include SampleMeasureCommon version '1.0.0' called Common

valueset "Sample Colorectal Cancer": 'http://example.org/fhir/ValueSet/sample-colorectal-cancer'
valueset "Sample Total Colectomy": 'http://example.org/fhir/ValueSet/sample-total-colectomy'
valueset "Sample Colonoscopy": 'http://example.org/fhir/ValueSet/sample-colonoscopy'
valueset "Sample Flexible Sigmoidoscopy": 'http://example.org/fhir/ValueSet/sample-flexible-sigmoidoscopy'
valueset "Sample CT Colonography": 'http://example.org/fhir/ValueSet/sample-ct-colonography'
valueset "Sample Stool DNA Test": 'http://example.org/fhir/ValueSet/sample-stool-dna-test'
valueset "Sample Fecal Immunochemical Test": 'http://example.org/fhir/ValueSet/sample-fecal-immunochemical-test'

parameter "Measurement Period" Interval<DateTime>

context Patient

/*
@description: Patients 45 to 75 at the end of the period with a qualifying visit in the period.
*/
define "Initial Population":
  Common."Age At End Of Period" in Interval[45, 75]
    and exists Common."Qualifying Encounters"

/*
@description: Everyone in the initial population.
*/
define "Denominator":
  "Initial Population"

/*
@description: Patients in hospice or palliative care, or with colorectal cancer or a total colectomy on or before
  the end of the period.
*/
define "Denominator Exclusions":
  Common."In Hospice Care"
    or Common."Has Palliative Care"
    or "Has Colorectal Cancer"
    or "Had Total Colectomy"

/*
@description: A colorectal cancer diagnosis, not refuted, that starts on or before the end of the period.
*/
define "Has Colorectal Cancer":
  exists (([ConditionProblemsHealthConcerns: "Sample Colorectal Cancer"]
    union [ConditionEncounterDiagnosis: "Sample Colorectal Cancer"]) Cancer
      where Cancer.isNotRefuted()
        and start of Cancer.prevalencePeriod() on or before day of end of "Measurement Period")

/*
@description: A completed total colectomy on or before the end of the period.
*/
define "Had Total Colectomy":
  exists ([Procedure: "Sample Total Colectomy"] Colectomy
    where Colectomy.status = 'completed'
      and end of Colectomy.performedPeriod() on or before day of end of "Measurement Period")

/*
@description: A completed colonoscopy in the measurement period or the nine years before it.
*/
define "Colonoscopy Within 10 Years":
  exists ([Procedure: "Sample Colonoscopy"] Colonoscopy
    where Colonoscopy.status = 'completed'
      and end of Colonoscopy.performedPeriod() during day of
        Interval[start of "Measurement Period" - 9 years, end of "Measurement Period"])

/*
@description: A completed flexible sigmoidoscopy in the measurement period or the four years before it.
*/
define "Flexible Sigmoidoscopy Within 5 Years":
  exists ([Procedure: "Sample Flexible Sigmoidoscopy"] Sigmoidoscopy
    where Sigmoidoscopy.status = 'completed'
      and end of Sigmoidoscopy.performedPeriod() during day of
        Interval[start of "Measurement Period" - 4 years, end of "Measurement Period"])

/*
@description: A completed CT colonography in the measurement period or the four years before it.
*/
define "CT Colonography Within 5 Years":
  exists ([Procedure: "Sample CT Colonography"] Colonography
    where Colonography.status = 'completed'
      and end of Colonography.performedPeriod() during day of
        Interval[start of "Measurement Period" - 4 years, end of "Measurement Period"])

/*
@description: A stool DNA test with a result, in the measurement period or the two years before it.
*/
define "Stool DNA Test Within 3 Years":
  exists ([LaboratoryResultObservation: "Sample Stool DNA Test"] StoolDna
    where StoolDna.status in { 'final', 'amended', 'corrected' }
      and StoolDna.value is not null
      and StoolDna.latestResult() during day of
        Interval[start of "Measurement Period" - 2 years, end of "Measurement Period"])

/*
@description: A fecal immunochemical test with a result, in the measurement period.
*/
define "Fecal Immunochemical Test In Period":
  exists ([LaboratoryResultObservation: "Sample Fecal Immunochemical Test"] Fit
    where Fit.status in { 'final', 'amended', 'corrected' }
      and Fit.value is not null
      and Fit.latestResult() during day of "Measurement Period")

/*
@description: Patients with any screening test inside its lookback.
*/
define "Numerator":
  "Colonoscopy Within 10 Years"
    or "Flexible Sigmoidoscopy Within 5 Years"
    or "CT Colonography Within 5 Years"
    or "Stool DNA Test Within 3 Years"
    or "Fecal Immunochemical Test In Period"
