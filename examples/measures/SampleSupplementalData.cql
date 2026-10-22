/*
@description: The supplemental data the sample measures report beside their populations: gender and payer.
@intent: CMS quality measures report supplemental data elements so results can be broken down by patient group.
@scope: QI-Core 6.0.0. Written for this repository; not derived from any published measure library.
@nonGoal: Race and ethnicity are not reported yet: the compiler does not read the nested US Core race and
  ethnicity extensions.
@guidance: The measurement period is passed in at run time.
*/
library SampleSupplementalData version '1.0.0'

using QICore version '6.0.0'

include FHIRHelpers version '4.0.1' called FHIRHelpers

parameter "Measurement Period" Interval<DateTime>

context Patient

/*
@description: The patient's administrative gender.
@guidance: The US Core birth sex extension is not read yet: the compiler files a field read off an extension
  under the extension's type rather than the patient's.
*/
define "SDE Sex":
  Patient.gender

/*
@description: The type of each of the patient's coverages that overlaps the measurement period.
*/
define "SDE Payer":
  [Coverage] Plan
    where Plan.period overlaps "Measurement Period"
    return Plan.type
